"""热币交易 · 香港服务器主机程序（常驻运行，systemd 管）。

四件事，各跑各的线程：
1. 定时取数：工作日 19:00 取当日数据上传后台；周日 18:00 取下周清单数据送审（北京时间，错过会补跑，补到窗口结束）。
2. 海报与台账任务：每 30 秒问后台有没有待办，有就在本机出图 / 写 Notion 台账（和 GitHub Actions 里的 job_runner 是同一份代码）。
3. 15 分钟监控：读 Telegram 里 /watch 设的清单，加上后台“今日监控计划”（当日分析的关键价位自动生成的规则），
   拉币安合约行情，条件到了发 Telegram 提醒（自动规则的提醒带分析上下文，并有节流，见 autowatch.py）。
4. 心跳：每 60 秒告诉后台“我活着”。超过 15 分钟没心跳，后台会发 Telegram 告警。

GitHub Actions 里的原定时任务保留当备用；后台对重复上传是幂等的（快照覆盖，周日清单重复送审会被拒收）。
密钥只放在服务器的环境文件里（默认 /etc/hotcoin/host.env，权限 600），不进仓库。
"""
import base64
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "1.0"
BJ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("HOTCOIN_ENV_FILE", "/etc/hotcoin/host.env"))
STATE_FILE = Path(os.environ.get("HOTCOIN_STATE_FILE", "/var/lib/hotcoin/state.json"))
RUN_DIR = Path(os.environ.get("HOTCOIN_RUN_DIR", "/var/lib/hotcoin/run"))
RESEARCH_QUEUE = ROOT / "research" / "queue.json"
RESEARCH_STATE_FILE = Path(os.environ.get("HOTCOIN_RESEARCH_STATE_FILE", str(STATE_FILE.parent / "research.json")))
AUTO_STATE_FILE = Path(os.environ.get("HOTCOIN_AUTO_STATE_FILE", str(STATE_FILE.parent / "auto_monitor.json")))
USER_AGENT = "hotcoin-host/" + VERSION
RETRY_GAP = timedelta(minutes=10)

# 定时任务：weekdays 是 Python 的 weekday()（周一=0 … 周日=6）；start 到 end 之间都算该跑（错过会补跑）。
SCHEDULE = {
    "collect": {"weekdays": (0, 1, 2, 3, 4), "start": (19, 0), "end": (19, 40)},
    "weekly": {"weekdays": (6,), "start": (18, 0), "end": (19, 0)},
}


def log(*parts):
    print(datetime.now(BJ).strftime("%m-%d %H:%M:%S"), *parts, flush=True)


# ---------- 配置与状态 ----------
def parse_env_text(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env(path=None, environ=os.environ):
    values = {}
    path = Path(path or ENV_FILE)
    if path.exists():
        values.update(parse_env_text(path.read_text(encoding="utf-8")))
    for key in ("WORKER_INGEST_URL", "PIPELINE_SECRET", "NOTION_TOKEN", "NOTION_API_KEY", "HOTCOIN_HTTP_PROXY",
                "HOTCOIN_AUTO_QUIET", "HOTCOIN_AUTO_CAP"):
        if environ.get(key) and key not in values:
            values[key] = environ[key]
    return values


def worker_base(env):
    parsed = urllib.parse.urlparse(env.get("WORKER_INGEST_URL", ""))
    loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
    if (parsed.scheme != "https" and not loopback) or not parsed.netloc:
        raise ValueError("WORKER_INGEST_URL 必须是 https:// 开头的完整网址")
    return f"{parsed.scheme}://{parsed.netloc}"


def load_state(path=None):
    path = Path(path or STATE_FILE)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    path = Path(path or STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------- 和后台说话 ----------
def worker_call(env, method, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(worker_base(env) + path, data=data, method=method, headers={
        "Authorization": "Bearer " + env.get("PIPELINE_SECRET", ""), "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"后台返回 {exc.code}：{exc.read().decode('utf-8', 'replace')[:200]}")


def send_document(env, name, data, caption=""):
    """把一个文件（bytes）发到审核人的 Telegram。"""
    worker_call(env, "POST", "/pipeline/document", {"name": name, "content_b64": base64.b64encode(data).decode(), "caption": caption}, timeout=120)


def run_research(env, state_path=None, queue_path=None, runner=None, sender=None, notifier=None, workdir=None):
    """研究任务队列：research/queue.json 里每个 id 只跑一次（失败最多再试一次），报告文件发到 Telegram。
    队列由仓库提供（我改代码、推送，主机 10 分钟内自己拉到），不用登录服务器。"""
    state_path, queue_path = state_path or RESEARCH_STATE_FILE, queue_path or RESEARCH_QUEUE
    runner, sender, notifier = runner or run_step, sender or send_document, notifier or notify
    try:
        queue = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    ran = []
    for job in queue:
        job_id, script = str(job.get("id", "")), str(job.get("script", ""))
        entry = state.setdefault(job_id, {"attempts": 0})
        if not job_id or entry.get("done") or entry["attempts"] >= 2:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]+\.py", script) or not (Path(queue_path).parent / script).is_file():
            entry["attempts"] = 2
            entry["error"] = "脚本名不合法或不存在"
            continue
        entry["attempts"] += 1
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state_path).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        out_dir = Path(workdir or tempfile.mkdtemp(prefix="research-")) / job_id
        title = str(job.get("title") or job_id)
        try:
            _, tail = runner([sys.executable, str(Path(queue_path).parent / script), "--out", str(out_dir), *[str(a) for a in job.get("args", [])]],
                             env, ROOT, timeout=1800)
            files = sorted(p for p in out_dir.iterdir() if p.is_file())
            if not files:
                raise RuntimeError("脚本跑完了但没有输出文件")
            for path in files:
                sender(env, path.name, path.read_bytes(), title if path == files[0] else "")
            notifier(env, f"研究报告已发送：{title}（{len(files)} 个文件）\n{tail.strip()[-300:]}")
            entry.update(done=datetime.now(BJ).isoformat(), error=None)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:300]
            try:
                notifier(env, f"研究任务失败：{title}（第 {entry['attempts']} 次）\n{str(exc)[:600]}" + ("\n10 分钟后自动再试一次" if entry["attempts"] < 2 else "\n不再自动重试"))
            except Exception:  # noqa: BLE001
                pass
        ran.append(job_id)
        Path(state_path).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return ran


def fetch_watchlist(env):
    return worker_call(env, "GET", "/pipeline/watchlist").get("rules") or []


def fetch_plan(env):
    """今日监控计划；后台还没生成过就是 None。"""
    return worker_call(env, "GET", "/pipeline/plan").get("plan")


def notify(env, text, fired=None):
    worker_call(env, "POST", "/pipeline/notify", {"text": text, "fired": list(fired or [])})


def heartbeat(env, tasks):
    worker_call(env, "POST", "/pipeline/heartbeat", {"host": socket.gethostname(), "version": VERSION, "tasks": tasks})


# ---------- 定时 ----------
def due(name, now, state):
    """该不该现在跑 name。now 是北京时间。返回 True / False。"""
    spec = SCHEDULE[name]
    if now.weekday() not in spec["weekdays"]:
        return False
    start = now.replace(hour=spec["start"][0], minute=spec["start"][1], second=0, microsecond=0)
    end = now.replace(hour=spec["end"][0], minute=spec["end"][1], second=0, microsecond=0)
    if not (start <= now <= end):
        return False
    entry = state.get(name) or {}
    day = now.strftime("%Y-%m-%d")
    if entry.get("done") == day:
        return False
    last_try = entry.get("tried_at")
    if entry.get("tried_day") == day and last_try:
        if now - datetime.fromisoformat(last_try) < RETRY_GAP:
            return False
    return True


def mark(name, now, ok, state):
    entry = state.setdefault(name, {})
    day = now.strftime("%Y-%m-%d")
    entry["tried_day"], entry["tried_at"] = day, now.isoformat()
    if ok:
        entry["done"] = day


def run_step(args, env, cwd, timeout=1500, check=True):
    """跑一个子进程，把环境文件里的密钥传进去；失败时抛出带原因的错误。"""
    child_env = {**os.environ, **env, "PYTHONUNBUFFERED": "1"}
    result = subprocess.run(args, cwd=cwd, env=child_env, capture_output=True, text=True, timeout=timeout)
    tail = (result.stdout + result.stderr)[-600:]
    for secret in (env.get("PIPELINE_SECRET"), env.get("NOTION_TOKEN"), env.get("NOTION_API_KEY")):
        if secret:
            tail = tail.replace(secret, "***")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(str(a) for a in args[:3])} 失败（退出码 {result.returncode}）：{tail}")
    return result.returncode, tail


def update_repo(env):
    """拉最新代码（仓库是公开的）。拉不下来就用现有的，不拦着取数。"""
    try:
        run_step(["git", "pull", "--ff-only", "-q"], env, ROOT, timeout=120)
    except Exception as exc:  # noqa: BLE001
        log("更新代码失败，沿用现有版本：", str(exc)[:200])


def calendar_open(env):
    code = ("import {beijingDay,eligible} from './calendar.mjs';const r=eligible(beijingDay());"
            "console.log(JSON.stringify(r));")
    _, out = run_step(["node", "--input-type=module", "-e", code], env, ROOT, timeout=60)
    return json.loads(out.strip().splitlines()[-1]).get("ok") is True


def task_collect(env):
    update_repo(env)
    if not calendar_open(env):
        log("今天不发布（日历），跳过取数")
        return
    out = RUN_DIR / "runtime"
    py = sys.executable
    run_step([py, "pipeline/run_data.py", "--output", str(out)], env, ROOT, timeout=2400)
    for script in ("fetch_ledger", "ledger_sync", "ledger_autofill", "poster_facts"):  # 这几步失败不拦上传（和 Actions 一致）
        try:
            run_step([py, f"pipeline/{script}.py", str(out)], env, ROOT, timeout=600)
        except Exception as exc:  # noqa: BLE001
            log(script, "失败，继续：", str(exc)[:200])
    run_step([py, "pipeline/upload_snapshot.py", str(out)], env, ROOT, timeout=300)


def task_weekly(env):
    update_repo(env)
    run_step([sys.executable, "pipeline/weekly_collect.py"], env, ROOT, timeout=1200)


TASKS = {"collect": task_collect, "weekly": task_weekly}


def self_update(env):
    """拉最新代码；host/ 或依赖文件有变化时返回 True（调用方负责重启）。依赖文件变了会先装依赖。"""
    _, before = run_step(["git", "rev-parse", "HEAD"], env, ROOT, timeout=30)
    run_step(["git", "pull", "--ff-only", "-q"], env, ROOT, timeout=120)
    _, after = run_step(["git", "rev-parse", "HEAD"], env, ROOT, timeout=30)
    before, after = before.strip(), after.strip()
    if before == after:
        return False
    _, names = run_step(["git", "diff", "--name-only", before, after], env, ROOT, timeout=30)
    files = [line.strip() for line in names.splitlines() if line.strip()]
    if any(f.endswith("requirements.txt") for f in files):
        run_step([sys.executable, "-m", "pip", "install", "-q", "-r", "pipeline/requirements.txt", "-r", "poster/requirements.txt"],
                 env, ROOT, timeout=900)
    return any(f.startswith("host/") or f.endswith("requirements.txt") for f in files)


def scheduler_tick(env, state, tasks, now=None, runner=None, state_path=None):
    """检查一遍定时任务，到点的跑一遍。返回跑了的任务名。"""
    now = now or datetime.now(BJ)
    ran = []
    for name in SCHEDULE:
        if not due(name, now, state):
            continue
        log("开始", name)
        try:
            (runner or tasks[name])(env)
            mark(name, now, True, state)
            tasks_status = "ok"
        except Exception as exc:  # noqa: BLE001
            mark(name, now, False, state)
            state[name]["error"] = str(exc)[:300]
            tasks_status = "failed"
            log(name, "失败：", str(exc)[:300])
        if tasks_status == "ok":
            state[name].pop("error", None)
        save_state(state, state_path)
        ran.append(name)
    return ran


# ---------- 线程 ----------
class Host:
    def __init__(self, env):
        self.env = env
        self.state = load_state()
        self.monitor_state = {}
        self.auto_state = None
        self.plan = None
        self.status = {"started": datetime.now(BJ).isoformat(), "monitor": None, "jobs": None, "scheduler": None}
        self.stop = threading.Event()
        self.work = threading.Lock()  # 有任务在跑时不更新、不重启

    def loop(self, name, interval, fn):
        while not self.stop.is_set():
            began = time.time()
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - 一个线程出错不影响别的
                self.status[name] = "错误：" + str(exc)[:120]
                log(name, "出错：", str(exc)[:300])
            self.stop.wait(max(1, interval - (time.time() - began)))

    def scheduler(self):
        with self.work:
            ran = scheduler_tick(self.env, self.state, TASKS)
        failed = [n for n in SCHEDULE if (self.state.get(n) or {}).get("error")]
        self.status["scheduler"] = ("失败：" + "、".join(failed)) if failed else ("ok" + (f"（刚跑 {','.join(ran)}）" if ran else ""))

    def poll_jobs(self):
        sys.path.insert(0, str(ROOT / "pipeline"))
        import job_runner
        token = self.env.get("NOTION_TOKEN") or self.env.get("NOTION_API_KEY") or ""
        base = job_runner.jobs_base(self.env["WORKER_INGEST_URL"])
        secret = self.env["PIPELINE_SECRET"]
        _, peek = job_runner.http("GET", base + "/next?peek=1", secret)
        if not peek.get("job"):
            self.status["jobs"] = "ok"
            return
        with self.work:
            update_repo(self.env)
            os.environ.update({k: v for k, v in self.env.items() if k in ("NOTION_TOKEN", "NOTION_API_KEY")})
            for _ in range(5):
                if not job_runner.run_one(base, secret, token):
                    break
        self.status["jobs"] = "ok（刚处理任务）"

    def updater(self):
        """每 10 分钟看一次仓库有没有新代码。host/ 里的程序变了就自己退出，systemd 十秒内拉起新版。"""
        if not self.work.acquire(blocking=False):
            return
        try:
            if self_update(self.env):
                log("主机程序有更新，重启加载新版")
                os._exit(0)
        finally:
            self.work.release()

    def current_plan(self):
        """读最新计划；后台暂时读不到就沿用上次读到的（过期与否由 autowatch 判断）。"""
        try:
            self.plan = fetch_plan(self.env)
        except Exception as exc:  # noqa: BLE001
            log("读监控计划失败，沿用上次的：", str(exc)[:200])
        return self.plan

    def monitor(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import autowatch
        import monitor
        parts = []
        try:
            summary = monitor.run_cycle(fetch_watchlist(self.env), self.monitor_state, lambda text, ids: notify(self.env, text, ids))
            parts.append(f"手动规则{summary['rules']}条，触发{summary['fired']}条" + (f"，错误{len(summary['errors'])}" if summary["errors"] else ""))
        except Exception as exc:  # noqa: BLE001 - 手动规则出错不拦自动规则
            parts.append("手动规则出错：" + str(exc)[:80])
            log("手动规则出错：", str(exc)[:300])
        try:
            if self.auto_state is None:
                self.auto_state = autowatch.load_state(AUTO_STATE_FILE)
            auto = autowatch.run_auto_cycle(self.current_plan(), self.auto_state, lambda text: notify(self.env, text),
                                            getter=monitor.http_json, settings=autowatch.settings_from_env(self.env))
            autowatch.save_state(self.auto_state, AUTO_STATE_FILE)
            parts.append(autowatch.status_text(auto))
            for error in auto["errors"][:3]:
                log("自动规则：", error)
        except Exception as exc:  # noqa: BLE001
            parts.append("自动规则出错：" + str(exc)[:80])
            log("自动规则出错：", str(exc)[:300])
        self.status["monitor"] = "；".join(parts)

    def research(self):
        with self.work:
            ran = run_research(self.env)
        self.status["research"] = f"刚跑 {','.join(ran)}" if ran else "ok"

    def beat(self):
        heartbeat(self.env, dict(self.status))

    def run(self):
        log("主机程序启动", VERSION, "仓库", ROOT)
        threads = [threading.Thread(target=self.loop, args=(n, i, f), daemon=True, name=n)
                   for n, i, f in (("scheduler", 20, self.scheduler), ("jobs", 30, self.poll_jobs),
                                   ("monitor", 15 * 60, self.monitor), ("heartbeat", 60, self.beat), ("research", 10 * 60, self.research),
                                   ("updater", 10 * 60, self.updater))]
        for thread in threads:
            thread.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            self.stop.set()


def main(argv):
    env = load_env()
    missing = [k for k in ("WORKER_INGEST_URL", "PIPELINE_SECRET") if not env.get(k)]
    if missing:
        print("环境文件缺少：" + "、".join(missing) + f"（文件：{ENV_FILE}）")
        return 1
    if "--once" in argv:  # 手动：立刻跑一轮监控（手动规则 + 自动规则）并打印；自动规则的状态照常写进本地状态文件
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import autowatch
        import monitor
        print(json.dumps(monitor.run_cycle(fetch_watchlist(env), {}, lambda text, ids: notify(env, text, ids)), ensure_ascii=False))
        state = autowatch.load_state(AUTO_STATE_FILE)
        auto = autowatch.run_auto_cycle(fetch_plan(env), state, lambda text: notify(env, text), getter=monitor.http_json,
                                        settings=autowatch.settings_from_env(env))
        autowatch.save_state(state, AUTO_STATE_FILE)
        print(json.dumps(auto, ensure_ascii=False))
        return 0
    if "--run" in argv:  # 手动：立刻跑一个定时任务，例如 --run collect
        name = argv[argv.index("--run") + 1]
        TASKS[name](env)
        return 0
    Host(env).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
