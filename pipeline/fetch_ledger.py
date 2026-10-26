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


LOG_HEADER = ["日期", "期号", "标的", "变更", "价格", "币价盈亏", "账户盈亏", "依据", "记录标记"]
TERMINAL = ("公开作废", "过期作废", "止损了结")
STOP_MOVE = "止损上移"


def parse_log(rows):
    """六、状态变更记录: append-only rows written by the program. Returns dicts keyed by header name."""
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:] if len(row) >= len(LOG_HEADER)]


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
    log = parse_log(find("六、"))
    # The log is append-only; the newest entry per (issue, symbol) decides the effective status.
    latest, moved, filled = {}, {}, {}
    for entry in log:
        key = (entry.get("期号"), entry.get("标的"))
        if entry.get("变更") == "成交" and entry.get("日期"):
            filled[key] = entry["日期"]
        if entry.get("变更") == STOP_MOVE:  # a stop move is not a status change; only the newest price matters
            price = to_number(entry.get("价格"))
            if price is not None:
                moved[key] = price
            continue
        latest[key] = entry.get("变更")
    header = active_rows[0]
    active = []
    for row in active_rows[1:]:
        record = {KEEP[name]: row[i] for i, name in enumerate(header) if name in KEEP and i < len(row)}
        change = latest.get((record.get("issue"), record.get("symbol")))
        if change in TERMINAL:
            continue
        if change == "成交":
            record["status"] = "持仓中"
        if record.get("status") in ACTIVE_STATES:
            for key in ("entry", "stop", "target", "size"):
                record[key] = to_number(record.get(key))
            # "stop" stays the published stop (never edited). "stop_now" is the moved stop from the log, or None when
            # no move was booked there (moves written only in a free-text note are NOT read: too easy to misparse).
            record["stop_now"] = moved.get((record.get("issue"), record.get("symbol")))
            record["filled"] = filled.get((record.get("issue"), record.get("symbol")))
            active.append(record)
    closed_recent = []
    for entry in reversed(log):  # newest log entries first
        if entry.get("变更") == "止损了结" and entry.get("账户盈亏"):
            closed_recent.append({"issue": entry["期号"], "symbol": entry["标的"],
                                  "pct": entry.get("币价盈亏") or "", "usd": usd_text(entry["账户盈亏"])})
    for row in (find("三、") or [])[1:]:
        if len(row) >= 14 and row[9] == "已了结":
            closed_recent.append({"issue": row[0], "symbol": row[2], "pct": row[12], "usd": usd_text(row[13])})
    closed_recent = [item for item in closed_recent if item["pct"][:1] in "+-"][:6]
    closed_stats = build_closed_stats(find("三、"), log, int(to_number(summary.get("已了结笔数")) or 0))
    updated = re.search(r"\d{4}-\d{2}-\d{2}", summary.get("更新至", ""))
    issue = re.search(r"第\s*(\d+)\s*期", summary.get("更新至", ""))
    counts = {"closed": int(to_number(summary.get("已了结笔数")) or 0),
              "open": sum(1 for r in active if r["status"] == "持仓中"),
              "pending": sum(1 for r in active if r["status"] == "挂单中")}
    return {"ok": True, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "updated_to": updated.group(0) if updated else None,
            "updated_issue": int(issue.group(1)) if issue else None,
            "equity_usd": to_number(summary.get("当前账户资金")),
            "cumulative_pnl_usd": to_number(summary.get("累计盈亏")),
            "counts": counts, "closed_recent": closed_recent, "closed_stats": closed_stats, "log_entries": len(log),
            "active": active}


def usd_value(text):
    """'+50.8 美元' / '-235.6 USD' -> 50.8 / -235.6; anything else -> None."""
    match = re.search(r"([+-]?\d[\d,]*\.?\d*)", text or "")
    return float(match.group(1).replace(",", "")) if match else None


def build_closed_stats(table, log, expected):
    """已了结笔数里赚几笔、亏几笔、最大一笔亏损。表三（已了结行）加状态变更记录里的止损了结，按（期号，标的）去重。
    笔数和汇总页的「已了结笔数」对不上就不给（None）：拿不到可信的数就不报，宁可缺也不编。"""
    trades = {}
    for row in (table or [])[1:]:
        if len(row) >= 14 and row[9] == "已了结":
            value = usd_value(row[13])
            if value is not None:
                trades.setdefault((row[0], row[2]), value)
    for entry in log:
        if entry.get("变更") == "止损了结":
            value = usd_value(entry.get("账户盈亏"))
            if value is not None:
                trades.setdefault((entry.get("期号"), entry.get("标的")), value)
    if not expected or len(trades) != expected:
        return None
    values = list(trades.values())
    losses = [v for v in values if v < 0]
    return {"wins": sum(1 for v in values if v > 0), "losses": len(losses),
            "worst_loss_usd": min(losses) if losses else None}


def usd_text(text):
    """'+50.8 美元' -> '+50.8 USD' (the poster's closed-trade cells)."""
    return re.sub(r"\s*美元", " USD", text or "").strip()


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
