"""Run jobs queued by the review Worker: render posters, write the Notion ledger.

Pull model: the Worker holds the job queue; this script (GitHub Actions) asks for the next job with the same
PIPELINE_SECRET the snapshot upload uses, does the work where the Notion token and Chromium live, and reports back.
No new secret is needed. `--peek` only reports whether a job is waiting (no lease), so the workflow can skip the
Chromium install on empty polls.
"""
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poster"))
sys.path.insert(0, str(ROOT / "pipeline"))
USER_AGENT = "hotcoin-trade-automation/1.0 (+github-actions)"


def annotate(level, title, message):
    flat = " ".join(str(message).split())[:900].replace("%", "%25")
    print(f"::{level} title={title}::{flat}", flush=True)


def jobs_base(ingest_url):
    parsed = urllib.parse.urlparse(ingest_url)
    loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")  # local end-to-end test only
    if (parsed.scheme != "https" and not loopback) or not parsed.netloc:
        raise ValueError("WORKER_INGEST_URL 必须是 https:// 开头的完整网址")
    return f"{parsed.scheme}://{parsed.netloc}/pipeline/jobs"


def http(method, url, secret, body=None, content_type="application/json", timeout=120):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + secret, "Content-Type": content_type, "Accept": "application/json",
        "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Worker HTTP {exc.code}: {text}")


def do_poster(job, base, secret, request=http):
    import render
    out = Path(tempfile.mkdtemp(prefix="posters-"))
    files = render.render(job["payload"], out)
    for name, path in files.items():
        request("POST", f"{base}/{urllib.parse.quote(job['id'], safe='')}/file?name={urllib.parse.quote(name)}", secret,
                path.read_bytes(), "image/png")
    return {"files": sorted(files)}


def do_ledger(job, token):
    import ledger_write
    return {"actions": ledger_write.apply(job["payload"], token=token)}


def run_one(base, secret, token, request=http):
    _, data = request("GET", base + "/next", secret)
    job = data.get("job")
    if not job:
        return False
    annotate("notice", "取到任务", f"{job['type']} {job['id']}")
    try:
        if job["type"] == "poster":
            result = do_poster(job, base, secret, request)
        elif job["type"] == "ledger":
            if not token:
                raise RuntimeError("没有设置 NOTION_TOKEN，无法写台账")
            result = do_ledger(job, token)
        else:
            raise RuntimeError("未知任务类型：" + str(job["type"]))
        request("POST", f"{base}/{urllib.parse.quote(job['id'], safe='')}/complete", secret, {"ok": True, "result": result})
        annotate("notice", "任务完成", f"{job['id']} {json.dumps(result, ensure_ascii=False)[:300]}")
    except Exception as exc:  # noqa: BLE001 - report the real reason to the reviewer
        message = str(exc).replace(token, "***") if token else str(exc)
        annotate("error", "任务失败", f"{job['id']}：{message}")
        request("POST", f"{base}/{urllib.parse.quote(job['id'], safe='')}/complete", secret, {"ok": False, "error": message[:400]})
    return True


def main(argv, request=http, environ=os.environ):
    secret, url = environ.get("PIPELINE_SECRET", "").strip(), environ.get("WORKER_INGEST_URL", "").strip()
    if not secret or not url:
        annotate("error", "任务轮询", "缺少 PIPELINE_SECRET / WORKER_INGEST_URL")
        return 1
    base = jobs_base(url)
    if "--peek" in argv:
        _, data = request("GET", base + "/next?peek=1", secret)
        has = bool(data.get("job"))
        if environ.get("GITHUB_OUTPUT"):
            with open(environ["GITHUB_OUTPUT"], "a") as handle:
                handle.write(f"has_job={'true' if has else 'false'}\n")
        print("has_job=", has)
        return 0
    token = environ.get("NOTION_TOKEN") or environ.get("NOTION_API_KEY") or ""
    for _ in range(5):
        if not run_one(base, secret, token, request):
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
