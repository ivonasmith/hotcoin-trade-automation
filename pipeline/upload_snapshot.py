"""Upload the latest collection snapshot to the review Worker.

Kept as a standalone script (not inline YAML) so it can be tested locally.
On failure it writes the server's real reply into a GitHub Actions annotation,
so the reason is visible on the run summary page without opening raw logs.
The secret is only ever sent in the Authorization header and is never printed.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

BEIJING = timezone(timedelta(hours=8))
USER_AGENT = "hotcoin-trade-automation/1.0 (+github-actions)"
RETRY_STATUS = {429, 500, 502, 503, 504}


def annotate(level, title, message):
    """Emit a GitHub Actions annotation (single line, truncated)."""
    flat = " ".join(str(message).split())[:900]
    flat = flat.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{flat}", flush=True)


def build_payload(runtime):
    attempts = sorted(Path(runtime).glob("collection-*"), key=lambda p: p.stat().st_mtime)
    if not attempts:
        raise RuntimeError("没有找到本次取数输出（runtime/collection-*）")
    latest = attempts[-1]
    report = json.loads((latest / "report.json").read_text())
    summary = json.loads((latest / "每日汇总.json").read_text())
    four = latest / "送审稿四行.txt"
    ledger_file = Path(runtime) / "ledger.json"
    ledger = json.loads(ledger_file.read_text()) if ledger_file.exists() else None
    return {
        "day": datetime.now(BEIJING).date().isoformat(),
        "report": report,
        "summary": summary,
        "four_lines": four.read_text() if four.exists() else "",
        "ledger": ledger,
    }


def post_once(url, secret, body, timeout=60):
    """Return (status, text). HTTP errors are returned, not raised."""
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def explain(status, text, path):
    hints = {
        401: "Worker 拒绝了授权头：PIPELINE_SECRET 与 Worker 里的值不一致。",
        403: "被拒绝：要么 PIPELINE_SECRET 与 Worker 不一致，要么请求被 Cloudflare 防护拦截（回复里若是 HTML 页面则为后者）。",
        404: f"路径不存在：WORKER_INGEST_URL 应以 /pipeline/snapshot 结尾，当前路径是 {path}。",
        405: f"方法不被允许：WORKER_INGEST_URL 应以 /pipeline/snapshot 结尾，当前路径是 {path}。",
        409: "快照超过 3 小时，Worker 认为已过期。",
        413: "快照体积超过 1.5MB 上限。",
    }
    return hints.get(status, "")


def main(runtime="runtime", sleep=time.sleep):
    url = os.environ.get("WORKER_INGEST_URL", "").strip()
    secret = os.environ.get("PIPELINE_SECRET", "").strip()
    if not url or not secret:
        missing = [n for n, v in (("WORKER_INGEST_URL", url), ("PIPELINE_SECRET", secret)) if not v]
        annotate("error", "快照上传失败", "缺少仓库 Secret：" + "、".join(missing))
        return 1
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        annotate("error", "快照上传失败", "WORKER_INGEST_URL 必须是 https:// 开头的完整网址（不要在日志里贴出它）。")
        return 1
    try:
        payload = build_payload(runtime)
    except Exception as exc:  # noqa: BLE001 - report any local problem clearly
        annotate("error", "快照上传失败", f"本地读取取数结果出错：{exc}")
        return 1
    body = json.dumps(payload, ensure_ascii=False).encode()
    metrics = payload["summary"].get("指标") or {}
    print(f"待上传：day={payload['day']} 大小={len(body)}字节 币种数={len(metrics)} "
          f"路径={parsed.path or '/'}", flush=True)

    last = (None, "")
    for attempt in range(1, 4):
        try:
            last = post_once(url, secret, body)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = (None, f"网络错误：{exc}")
        status, text = last
        if status == 200:
            try:
                result = json.loads(text)
            except ValueError:
                result = {}
            if result.get("ok"):
                print("快照上传完成：", result.get("day"), flush=True)
                annotate("notice", "快照上传成功", f"day={result.get('day')} 大小={len(body)}字节")
                return 0
        if status in RETRY_STATUS or status is None:
            print(f"第{attempt}次失败（{status}），稍后重试", flush=True)
            if attempt < 3:
                sleep(5 * attempt)
            continue
        break
    status, text = last
    detail = explain(status, text, parsed.path or "/")
    annotate("error", "快照上传失败",
             f"HTTP {status}；服务器回复：{text[:400]}；{detail}".rstrip("；"))
    return 1


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2])))
