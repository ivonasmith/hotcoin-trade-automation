"""周日「下周清单」：取数 → 生成数据包和中英文帖子 → 交给审核后台（Worker）。

GitHub Actions 每周日北京时间 15:17 跑一次（weekly.yml）。取三样东西：
  1. 事件日历 uncleonchain.com/data/cal/cal_latest.json（备用：GitHub 原始文件）
  2. Notion 台账（和工作日同一页、同一个令牌）
  3. 持仓币的最新已收盘日线（和工作日海报同一个行情脚本）
任何一样拿不到就不出稿，并且给审核人发一条 Telegram 说明原因——绝不静悄悄地没动静，也不拿旧数据凑。
不调用模型：文字全部由 poster/weekly_pack.py 按数据拼出，数字只有一个来源。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poster"))
sys.path.insert(0, str(ROOT / "pipeline"))
BEIJING = timezone(timedelta(hours=8))
USER_AGENT = "hotcoin-trade-automation/1.0 (+github-actions)"
CAL_URLS = ["https://uncleonchain.com/data/cal/cal_latest.json",
            "https://raw.githubusercontent.com/ivonasmith/uncleonchain/main/site/data/cal/cal_latest.json"]


def annotate(level, title, message):
    flat = " ".join(str(message).split())[:900].replace("%", "%25")
    print(f"::{level} title={title}::{flat}", flush=True)


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_calendar(fetch=http_json):
    errors = []
    for url in CAL_URLS:
        try:
            data = fetch(url)
            if isinstance(data, dict) and (data.get("btc") or {}).get("events") is not None and data.get("today"):
                return data
            errors.append(f"{url} 格式不对")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url} {exc}")
    raise RuntimeError("事件日历读取失败：" + "；".join(errors))


# ───────────────────────── 台账 ─────────────────────────
def read_ledger(token, url, get):
    """返回 (ledger, log, notes)。ledger = fetch_ledger.build_ledger 的结果 + rows_all（二、三两表所有单子的发布日和方向）。"""
    import fetch_ledger as fl
    blocks = fl.children(fl.page_id(url), token, get)
    sections = fl.parse_sections(blocks, token, get)
    ledger = fl.build_ledger(sections)

    def find(prefix):
        for title, rows in sections.items():
            if title.startswith(prefix):
                return rows
        return None
    rows_all, notes = [], {}
    for prefix in ("二、", "三、"):
        table = find(prefix)
        if not table:
            continue
        header = table[0]
        for row in table[1:]:
            rec = {name: row[i] for i, name in enumerate(header) if i < len(row)}
            if not rec.get("标的"):
                continue
            rows_all.append({"issue": rec.get("期号"), "day": rec.get("发布日"), "symbol": rec["标的"], "side": rec.get("方向")})
            if prefix == "二、":
                notes[(rec.get("期号"), rec["标的"])] = rec.get("备注", "")
    ledger["rows_all"] = rows_all
    return ledger, fl.parse_log(find("六、")), notes


# ───────────────────────── 行情：最新已收盘日线 ─────────────────────────
def latest_closes(symbols, klines=None, now=None):
    """每个币取最近一根「已经收盘」的日线收盘价（UTC 日线，北京时间 08:00 收盘）。返回 ({符号: {'close':..}}, 日期)。"""
    if klines is None:
        sys.path.insert(0, str(ROOT / "pipeline" / "vendor"))
        import 拉数据  # vendor：合约行情，带备用域名
        klines = 拉数据.klines
    now = now or datetime.now(timezone.utc)
    today_utc = now.date()
    out, day = {}, None
    for sym in symbols:
        try:
            bars = klines(f"{sym}USDT", "1d", 6)
        except Exception as exc:  # noqa: BLE001
            annotate("warning", "行情未取到", f"{sym}：{exc}")
            continue
        done = [b for b in bars if datetime.fromtimestamp(int(b[0]) / 1000, timezone.utc).date() < today_utc]
        if not done:
            continue
        bar = done[-1]
        out[sym] = {"close": float(bar[4])}
        d = datetime.fromtimestamp(int(bar[0]) / 1000, timezone.utc).date().isoformat()
        day = d if day is None else min(day, d)
    return out, day


# ───────────────────────── 生成与上传 ─────────────────────────
def weekly_url(ingest_url):
    parsed = urllib.parse.urlparse(ingest_url)
    loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
    if (parsed.scheme != "https" and not loopback) or not parsed.netloc:
        raise ValueError("WORKER_INGEST_URL 必须是 https:// 开头的完整网址")
    return f"{parsed.scheme}://{parsed.netloc}/pipeline/weekly"


def post_json(url, secret, body, timeout=60):
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + secret, "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def send(url, secret, body, post=post_json, sleep=None):
    last = (None, "")
    for attempt in range(1, 4):
        try:
            last = post(url, secret, body)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = (None, f"网络错误：{exc}")
        if last[0] == 200:
            return last
        if last[0] is not None and last[0] < 500 and last[0] != 429:
            return last
        (sleep or time.sleep)(5 * attempt)
    return last


def build(today, env, fetch=http_json, notion_get=None, klines=None, now=None):
    """取数并生成 (pack, zh, en)。取不到关键数据就抛 RuntimeError，由 main 转告审核人。"""
    import fetch_ledger as fl
    import weekly_pack as wp
    cal = load_calendar(fetch)
    token = env.get("NOTION_TOKEN") or env.get("NOTION_API_KEY") or ""
    if not token:
        raise RuntimeError("没有设置 NOTION_TOKEN，台账读不了")
    try:
        ledger, log, notes = read_ledger(token, env.get("TRADE_LOG_URL") or fl.DEFAULT_LEDGER_URL, notion_get or fl.notion_get)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("台账读取失败：" + str(exc).replace(token, "***"))
    symbols = [a["symbol"] for a in ledger["active"] if a.get("status") == "持仓中"]
    prices, px_date = latest_closes(symbols, klines, now)
    if symbols and not prices:
        raise RuntimeError("持仓币的收盘价一个都没取到，浮盈算不出来")
    pack = wp.build_pack(today, cal, ledger, log, notes, prices, px_date or today)
    zh, en = wp.compose_posts(pack)
    return pack, zh, en


def main(argv, env=os.environ, **inject):
    secret, ingest = env.get("PIPELINE_SECRET", "").strip(), env.get("WORKER_INGEST_URL", "").strip()
    out = None
    if "--out" in argv:
        out = Path(argv[argv.index("--out") + 1])
    day = None
    if "--day" in argv:
        day = argv[argv.index("--day") + 1]
    today = day or datetime.now(BEIJING).date().isoformat()
    url = None
    if out is None:
        if not secret or not ingest:
            annotate("error", "周日清单", "缺少 PIPELINE_SECRET / WORKER_INGEST_URL")
            return 1
        url = weekly_url(ingest)
    post = inject.pop("post", post_json)
    try:
        pack, zh, en = build(today, env, **inject)
        for w in pack["warnings"]:
            annotate("warning", "周日清单提示", w)
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)
            (out / "pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
            (out / "post_zh.txt").write_text(zh, encoding="utf-8")
            (out / "post_en.txt").write_text(en, encoding="utf-8")
            print(zh + "\n-----\n" + en)
            return 0
        status, text = send(url, secret, {"day": today, "pack": pack, "zh_post": zh, "en_post": en}, post=post)
        if status != 200:
            annotate("error", "周日清单上传失败", f"HTTP {status} {text[:300]}")
            return 1
        annotate("notice", "周日清单已送审", f"{today}，提示 {len(pack['warnings'])} 条")
        return 0
    except Exception as exc:  # noqa: BLE001 - 失败一定要让审核人知道
        message = str(exc)
        annotate("error", "周日清单没有生成", message)
        if url:
            try:
                send(url, secret, {"day": today, "error": message[:500]}, post=post)
            except Exception:  # noqa: BLE001
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
