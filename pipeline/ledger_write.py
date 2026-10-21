"""Write approved orders and status changes into the Notion ledger. Append-only, safe to run twice.

What it does, and what it never does:
- A newly approved order is appended as a new row to 二、当前在场 and its if-then conditions to 四、条件单登记簿.
- Fills / expiries / cancellations / stop-outs found by ledger_sync are appended to 六、状态变更记录 (created on
  first use). Original rows (进场/止损/目标/条件) are never edited or deleted.
- 一、汇总 counters and the 更新至 text are updated; the old 更新至 text is kept after "上期记录：".
- Idempotency: every entry has a key "desk:<day>:<issue>:<symbol>:<kind>". Keys are written into the 更新至 text
  (summary applied) and into the new row / log row (record written), so a retry never duplicates either.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_ledger as fl  # noqa: E402

LOG_TITLE = "六、状态变更记录"
LOG_NOTE = "程序自动追加，只加不改：成交、作废、过期、止损了结都记在这里，上面各表里的原始记录保持不动。"
KINDS = {"fill": "成交", "cancel": "公开作废", "expire": "过期作废", "stop_out": "止损了结"}
BASE_USD = 2000


def rich(text):
    text = str(text)
    return [{"type": "text", "text": {"content": text[i:i + 1900]}} for i in range(0, max(len(text), 1), 1900)] if text else []


def plain_number(value):
    """2585.57 -> '2585.57', 0.088303 -> '0.088303' (the ledger writes prices without thousands separators)."""
    text = format(Decimal(str(value)).normalize(), "f")
    return text


def notion_request(token):
    def call(method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request("https://api.notion.com/v1" + path, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Notion-Version": fl.NOTION_VERSION, "Content-Type": "application/json",
            "User-Agent": "hotcoin-trade-automation/1.0 (+github-actions)"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace")[:300]
                if exc.code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                hint = {401: "Notion令牌无效", 403: "令牌没有写入权限：台账页面的集成需要“更新内容/插入内容”权限",
                        404: "找不到页面：需要把集成加到台账页面的连接里"}.get(exc.code, "")
                raise RuntimeError(f"Notion HTTP {exc.code} {hint} {text}".strip())
    return call


class Page:
    """Top-level layout of the ledger page: table block ids and rows (with ids) per section number."""

    def __init__(self, call, page_id):
        self.call, self.page_id = call, page_id
        self.blocks = self._children(page_id)
        self.sections = {}
        current = None
        for block in self.blocks:
            kind = block.get("type")
            if kind in ("heading_1", "heading_2", "heading_3"):
                current = fl.plain(block[kind]["rich_text"])
            elif kind == "table" and current and current not in self.sections:
                self.sections[current] = {"id": block["id"], "width": block["table"]["table_width"],
                                          "rows": self._rows(block["id"])}

    def _children(self, block_id):
        out, cursor = [], None
        while True:
            data = self.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
            out.extend(data.get("results", []))
            if not data.get("has_more"):
                return out
            cursor = data.get("next_cursor")

    def _rows(self, table_id):
        return [{"id": r["id"], "cells": r["table_row"]["cells"], "text": [fl.plain(c) for c in r["table_row"]["cells"]]}
                for r in self._children(table_id) if r.get("type") == "table_row"]

    def section(self, prefix):
        for title, info in self.sections.items():
            if title.startswith(prefix):
                return info
        return None

    def refresh(self, prefix):
        info = self.section(prefix)
        if info:
            info["rows"] = self._rows(info["id"])
        return info


def table_row(cells):
    return {"object": "block", "type": "table_row", "table_row": {"cells": [rich(c) for c in cells]}}


def append_rows(call, table, rows):
    for row in rows:
        if len(row) != table["width"]:
            raise RuntimeError(f"新行列数 {len(row)} 与表格列数 {table['width']} 不一致，不写入")
    call("PATCH", f"/blocks/{table['id']}/children", {"children": [table_row(r) for r in rows]})


def ensure_log(page, call):
    info = page.section("六、")
    if info:
        return info
    # Put the new section before the closing signature line, after section 五.
    anchor = page.blocks[-2]["id"] if len(page.blocks) >= 2 else None
    children = [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": rich(LOG_TITLE)}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich(LOG_NOTE)}},
        {"object": "block", "type": "table", "table": {"table_width": len(fl.LOG_HEADER), "has_column_header": True,
                                                       "has_row_header": False, "children": [table_row(fl.LOG_HEADER)]}},
    ]
    body = {"children": children}
    try:
        call("PATCH", f"/blocks/{page.page_id}/children", dict(body, after=anchor) if anchor else body)
    except RuntimeError:
        call("PATCH", f"/blocks/{page.page_id}/children", body)  # older API: append at the end instead
    fresh = Page(call, page.page_id)
    page.blocks, page.sections = fresh.blocks, fresh.sections
    return page.section("六、")


def find_row(rows, want):
    for row in rows[1:]:
        text = row["text"]
        if all(i < len(text) and text[i] == value for i, value in want.items()):
            return row
    return None


def summary_editor(page, call):
    info = page.refresh("一、")
    rows = {r["text"][0]: r for r in info["rows"][1:] if r["text"]}

    def num(label):
        return fl.to_number(rows[label]["text"][1])

    def put(label, text):
        row = rows[label]
        cells = row["cells"]
        cells[1] = rich(text)
        call("PATCH", f"/blocks/{row['id']}", {"table_row": {"cells": cells}})
        row["text"][1] = text

    return rows, num, put


COUNTERS = {"已了结笔数": "closed", "持仓中笔数": "open", "挂单未成交": "pending", "公开作废": "cancel", "过期未成交": "expire"}


def apply_summary(page, call, deltas, record):
    """deltas: dict of counters; record: text for 更新至. Returns True when something was written."""
    rows, num, put = summary_editor(page, call)
    counters = COUNTERS
    for label, key in counters.items():
        if deltas.get(key) and label in rows:
            put(label, f"{int(num(label) + deltas[key])} 笔")
    if deltas.get("pnl") and "累计盈亏" in rows:
        cumulative = round(num("累计盈亏") + deltas["pnl"], 2)
        put("累计盈亏", f"{cumulative:+,.2f} 美元")
        if "当前账户资金" in rows:
            put("当前账户资金", f"{round(num('当前账户资金') + deltas['pnl'], 2):,.2f} 美元")
        if "账户收益率" in rows:
            put("账户收益率", f"{cumulative / 10000 * 100:+.2f}%")
    old = rows["更新至"]["text"][1] if "更新至" in rows else ""
    put("更新至", f"{record}；上期记录：{old}" if old else record)
    return True


def build_log_row(day, issue, symbol, kind, price, pct, usd, basis, key):
    return [day, str(issue), symbol, KINDS[kind], price, pct, usd, basis, key]


def status_entries(day, proposals, active_rows):
    """Turn ledger_sync proposals into log rows + summary deltas."""
    out = []
    for item in proposals:
        kind = item.get("type")
        if kind not in KINDS:
            continue
        issue, symbol = str(item.get("issue")), item.get("symbol")
        row = find_row(active_rows, {0: issue, 2: symbol})
        if not row:
            continue
        text = row["text"]
        key = f"desk:{item.get('date') or day}:{issue}:{symbol}:{kind}"
        when = item.get("date") or day
        entry = fl.to_number(text[6]) if len(text) > 6 else None
        size = fl.to_number(text[5]) if len(text) > 5 else None
        short = len(text) > 4 and text[4] == "做空"
        price, pct, usd, deltas = "—", "—", "—", {}
        if kind == "fill":
            price = plain_number(entry) if entry else "—"
            deltas = {"pending": -1, "open": 1}
        elif kind == "cancel":
            deltas = {"pending": -1, "cancel": 1}
        elif kind == "expire":
            deltas = {"pending": -1, "expire": 1}
        elif kind == "stop_out":
            close = item.get("price")
            if not (close and entry and size):
                continue
            gain = (entry - close) / entry if short else (close - entry) / entry
            money = round(BASE_USD * size * gain, 1)
            price, pct, usd = plain_number(close), f"{gain * 100:+.2f}%", f"{money:+.1f} 美元"
            deltas = {"open": -1, "closed": 1, "pnl": money}
        out.append({"key": key, "row": build_log_row(when, issue, symbol, kind, price, pct, usd, item.get("text", ""), key),
                    "deltas": deltas, "desc": f"第{issue}期{symbol}{KINDS[kind]}"})
    return out


def order_row(payload):
    o = payload["order"]
    key = f"desk:{payload['day']}:{payload['issue']}:{o['symbol']}:new"
    note = f"{payload.get('note', '')} [{key}]".strip()
    return key, [str(payload["issue"]), payload["day"], o["symbol"], o.get("tier", "慢档日线"), "做空",
                 f"{o['size']:.2f}", plain_number(o["entry"]), plain_number(o["stop"]), plain_number(o["target"]),
                 "挂单中", "—", "—", "—", "—", note]


def condition_rows(payload):
    return [[str(payload["issue"]), payload["order"]["symbol"], c["condition"], c["action"], c["triggered"], c["conclusion"]]
            for c in payload.get("conditions", [])]


def condition_backfill_rows(payload):
    """One-off corrections for an existing order: append missing conditions, never edit prior rows."""
    return [[str(c.get("issue", payload["issue"])), c["symbol"], c["condition"], c["action"],
             c["triggered"], c["conclusion"]] for c in payload.get("condition_backfills", [])]


def apply(payload, token=None, call=None, page_url=None):
    """Returns a list of human-readable actions taken (empty if everything was already written)."""
    call = call or notion_request(token)
    page = Page(call, fl.page_id(page_url or fl.DEFAULT_LEDGER_URL))
    day, issue = payload["day"], payload["issue"]
    active = page.section("二、")
    if not active:
        raise RuntimeError("台账里没找到“二、当前在场”表格，不写入")
    log_info = page.section("六、")
    log_keys = {r["text"][8] for r in (log_info["rows"][1:] if log_info else []) if len(r["text"]) > 8}
    summary_text = (find_row(page.section("一、")["rows"], {0: "更新至"}) or {"text": ["", ""]})["text"][1]
    actions, deltas_total, descs = [], {}, []

    registry = page.section("四、")
    backfills = condition_backfill_rows(payload)
    if backfills:
        if not registry:
            raise RuntimeError("台账里没找到“四、条件单登记簿”表格，不写入")
        fresh = [r for r in backfills if not find_row(registry["rows"], {0: r[0], 1: r[1], 2: r[2]})]
        if fresh:
            append_rows(call, registry, fresh)
            actions.append(f"条件单登记簿补写{len(fresh)}条")
            registry = page.refresh("四、")

    entries = [e for e in status_entries(day, payload.get("proposals") or [], active["rows"]) if e["key"] not in log_keys]
    new_key, new_row = (order_row(payload) if payload.get("order") else (None, None))
    order_exists = bool(new_row) and any(
        find_row(page.section(p)["rows"], {0: str(issue), 2: payload["order"]["symbol"]})
        for p in ("二、", "三、") if page.section(p))
    if new_row and not order_exists and new_key not in summary_text:
        deltas_total["pending"] = deltas_total.get("pending", 0) + 1
        descs.append(f"新增{payload['order']['symbol']}做空限价挂单1张")
    for entry in entries:
        if entry["key"] in summary_text:
            continue  # summary already applied on an earlier attempt; only the log row is missing
        for k, v in entry["deltas"].items():
            deltas_total[k] = deltas_total.get(k, 0) + v
        descs.append(entry["desc"])
    keys = [e["key"] for e in entries if e["key"] not in summary_text] + ([new_key] if new_row and not order_exists and new_key not in summary_text else [])
    if keys:
        record = f"第 {issue} 期 · {day}（{'；'.join(descs)}；程序自动写入 [{','.join(keys)}]）"
        apply_summary(page, call, deltas_total, record)
        actions.append("更新汇总：" + "；".join(descs))

    if entries:
        log = ensure_log(page, call)
        append_rows(call, log, [e["row"] for e in entries])
        actions.append(f"状态变更记录追加{len(entries)}行")
    if new_row and not order_exists:
        # Conditions first, the order row last: the row is the commit point a retry checks.
        registry = page.section("四、")
        wanted = condition_rows(payload)
        if registry and wanted:
            fresh = [r for r in wanted if not find_row(registry["rows"], {0: r[0], 1: r[1], 2: r[2]})]
            if fresh:
                append_rows(call, registry, fresh)
                actions.append(f"条件单登记簿新增{len(fresh)}条")
        append_rows(call, page.section("二、"), [new_row])
        actions.append(f"当前在场新增 {payload['order']['symbol']} 第{issue}期")
    return actions


def apply_fills(proposals, day, token=None, call=None, page_url=None):
    """Unattended write used by the daily data collection: a fill is a market fact (the price traded through the
    entry), so it must not wait for the next issue to be approved. Writes ONLY fills, and only the status log row
    (the reader derives the status from it) plus the two counters. The 更新至 text is left alone on purpose: the issue
    numbering reads it, and a fill is not an issue. The same key as the issue-time ledger job is used, so whichever
    runs second skips it. Order: log row first (commit point), then counters; a crash in between leaves a counter one off
    (display only) but never a duplicate row."""
    call = call or notion_request(token)
    page = Page(call, fl.page_id(page_url or fl.DEFAULT_LEDGER_URL))
    active = page.section("二、")
    if not active:
        raise RuntimeError("台账里没找到“二、当前在场”表格，不写入")
    log_info = page.section("六、")
    log_keys = {r["text"][8] for r in (log_info["rows"][1:] if log_info else []) if len(r["text"]) > 8}
    summary_text = (find_row(page.section("一、")["rows"], {0: "更新至"}) or {"text": ["", ""]})["text"][1]
    fills = [p for p in proposals if p.get("type") == "fill"]
    entries = [e for e in status_entries(day, fills, active["rows"]) if e["key"] not in log_keys]
    if not entries:
        return []
    log = ensure_log(page, call)
    append_rows(call, log, [e["row"] for e in entries])
    deltas = {}
    for entry in entries:
        if entry["key"] in summary_text:
            continue  # the issue-time job already counted it before it crashed
        for k, v in entry["deltas"].items():
            deltas[k] = deltas.get(k, 0) + v
    if deltas:
        rows, num, put = summary_editor(page, call)
        for label, key in COUNTERS.items():
            if deltas.get(key) and label in rows:
                put(label, f"{int(num(label) + deltas[key])} 笔")
    return [e["desc"] for e in entries]


def main(job_payload, token):
    return apply(job_payload, token=token)


if __name__ == "__main__":
    print(json.dumps(main(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2]), ensure_ascii=False))
