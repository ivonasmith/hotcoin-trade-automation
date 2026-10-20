"""Read the trade ledger from the Notion page and write runtime/ledger.json.

The ledger is a normal Notion page with tables (汇总 / 当前在场 / 已结束), not a database,
so it is read through the blocks API. Needs a Notion integration token (GitHub secret
NOTION_TOKEN) and the ledger page shared with that integration. Never fails the workflow:
on any problem it writes {"ok": false, "error": ...} so the review bot falls back to
"ledger not available, check by hand".
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_LEDGER_URL = "https://bottlenose-muse-07e.notion.site/HotCoinDesk-Trade-Record-3cfb72fc8cae808d9deecfdc21f4cba5"
NOTION_VERSION = "2022-06-28"
ACTIVE_STATES = ("挂单中", "持仓中")
KEEP = {"期号": "issue", "发布日": "day", "标的": "symbol", "档位": "tier", "方向": "side", "仓位": "size",
        "进场": "entry", "止损（收盘）": "stop", "目标": "target", "状态": "status"}


def annotate(level, title, message):
    flat = " ".join(str(message).split())[:900].replace("%", "%25")
    print(f"::{level} title={title}::{flat}", flush=True)


def page_id(url):
    last = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", "")
    found = re.search(r"([0-9a-fA-F]{32})$", last)
    if not found:
        raise ValueError("台账地址里找不到页面编号")
    return found.group(1).lower()


def notion_get(path, token):
    req = urllib.request.Request("https://api.notion.com/v1" + path, headers={
        "Authorization": "Bearer " + token, "Notion-Version": NOTION_VERSION,
        "User-Agent": "hotcoin-trade-automation/1.0 (+github-actions)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        hint = {401: "Notion令牌无效", 403: "令牌没有读取权限",
                404: "找不到页面：需要在台账页面右上角“…→连接”里把这个集成加进去"}.get(exc.code, "")
        raise RuntimeError(f"Notion HTTP {exc.code} {hint} {body}".strip())


def children(block_id, token, get):
    results, cursor = [], None
    while True:
        query = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        data = get(f"/blocks/{block_id}/children{query}", token)
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            return results
        cursor = data.get("next_cursor")


def plain(rich):
    return "".join(part.get("plain_text", "") for part in rich or []).strip()


def read_table(block, token, get):
    rows = []
    for row in children(block["id"], token, get):
        if row.get("type") == "table_row":
            rows.append([plain(cell) for cell in row["table_row"]["cells"]])
    return rows


def to_number(text):
    found = re.search(r"-?\d[\d,]*\.?\d*", text or "")
    return float(found.group(0).replace(",", "")) if found else None


def parse_sections(blocks, token, get):
    sections, current = {}, None
    for block in blocks:
        kind = block.get("type")
        if kind in ("heading_1", "heading_2", "heading_3"):
            current = plain(block[kind]["rich_text"])
        elif kind == "table" and current:
            sections.setdefault(current, read_table(block, token, get))
    return sections


def build_ledger(sections):
    def find(prefix):
        for title, rows in sections.items():
            if title.startswith(prefix):
                return rows
        return None
    summary_rows, active_rows = find("一、"), find("二、")
    if summary_rows is None or active_rows is None:
        raise RuntimeError("台账页面里没找到“一、汇总”或“二、当前在场”表格")
    summary = {row[0]: row[1] for row in summary_rows[1:] if len(row) >= 2}
    header = active_rows[0]
    active = []
    for row in active_rows[1:]:
        record = {KEEP[name]: row[i] for i, name in enumerate(header) if name in KEEP and i < len(row)}
        if record.get("status") in ACTIVE_STATES:
            for key in ("entry", "stop", "target", "size"):
                record[key] = to_number(record.get(key))
            active.append(record)
    updated = re.search(r"\d{4}-\d{2}-\d{2}", summary.get("更新至", ""))
    return {"ok": True, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "updated_to": updated.group(0) if updated else None,
            "equity_usd": to_number(summary.get("当前账户资金")),
            "cumulative_pnl_usd": to_number(summary.get("累计盈亏")),
            "active": active}


def fetch_ledger(url, token, get=notion_get):
    blocks = children(page_id(url), token, get)
    return build_ledger(parse_sections(blocks, token, get))


def main(runtime="runtime", get=notion_get):
    token = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY") or ""
    url = os.environ.get("TRADE_LOG_URL") or DEFAULT_LEDGER_URL
    target = Path(runtime) / "ledger.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not token:
            raise RuntimeError("没有设置 NOTION_TOKEN（GitHub 仓库 Secrets），台账不读取")
        ledger = fetch_ledger(url, token, get)
        annotate("notice", "台账已读取", f"在场{len(ledger['active'])}张，更新至{ledger['updated_to']}")
    except Exception as exc:  # noqa: BLE001 - must never block the snapshot upload
        ledger = {"ok": False, "error": str(exc)[:300]}
        annotate("warning", "台账未读取", str(exc).replace(token, "***") if token else str(exc))
    target.write_text(json.dumps(ledger, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "runtime"))
