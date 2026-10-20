"""Check the ledger's live orders against market data and list what should change.

Dry-run only: it never writes to Notion. Output goes to runtime/ledger.json under "proposals" so the
review draft can tell the reviewer which ledger rows are out of date. Rules follow 《操作规格》第08节 and the
ledger page itself: entry is "touched" intraday, stops are judged on the daily close, short orders carry the
registered cancel conditions (funding turns negative / daily close above MA50), orders expire after 15 days.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
BEIJING = timezone(timedelta(hours=8))
VALID_DAYS = 15


def utc_day(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).date()


def _to_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def reconcile(rows, bars_by_symbol, metrics, today):
    """rows: ledger["active"]; bars_by_symbol: {sym: [(ts,o,h,l,c,v)...] oldest first, last bar may be unfinished};
    metrics: summary["指标"]; today: Beijing date. Returns a list of proposal dicts."""
    out = []
    for row in rows:
        sym, side, status = row.get("symbol"), row.get("side"), row.get("status")
        issue = row.get("issue")
        published = _to_date(row.get("day"))
        entry, stop = row.get("entry"), row.get("stop")
        bars = bars_by_symbol.get(sym) or []
        if not (published and entry and stop and bars):
            out.append({"issue": issue, "symbol": sym, "type": "check", "text": f"第{issue}期 {sym}：缺少价位或行情，程序无法核对"})
            continue
        short = side == "做空"
        touched = lambda b: (b[2] >= entry) if short else (b[3] <= entry)  # noqa: E731
        since = [b for b in bars if utc_day(b[0]) >= published]
        first_touch = next((b for b in since if touched(b)), None)
        done = bars[:-1]  # the last bar is today's, still forming
        close = done[-1][4] if done else None
        close_day = utc_day(done[-1][0]) if done else None
        if status == "挂单中":
            expiry = published + timedelta(days=VALID_DAYS)
            if first_touch is not None:
                hit = utc_day(first_touch[0])
                note = "（发布当天触及，台账里这类情况口径不一，请人工确认）" if hit == published else ""
                out.append({"issue": issue, "symbol": sym, "type": "fill", "date": hit.isoformat(),
                            "text": f"第{issue}期 {sym}{side}：{hit.isoformat()} 盘中已触及进场 {entry:g}，应记为成交{note}"})
                continue
            if today > expiry:
                out.append({"issue": issue, "symbol": sym, "type": "expire",
                            "text": f"第{issue}期 {sym}{side}：{VALID_DAYS}天有效期已过（到 {expiry.isoformat()}）未成交，应记过期作废"})
                continue
            if short:
                m = metrics.get(sym) or {}
                if m.get("fund_ann") is not None and m["fund_ann"] < 0:
                    out.append({"issue": issue, "symbol": sym, "type": "cancel",
                                "text": f"第{issue}期 {sym}空单：当前资金费率年化 {m['fund_ann']:.1f}% 为负，触发“成交前费率转负则撤单”，应公开作废"})
                    continue
                if m.get("ma50") and close is not None and close >= m["ma50"]:
                    out.append({"issue": issue, "symbol": sym, "type": "cancel",
                                "text": f"第{issue}期 {sym}空单：{close_day} 日线收盘 {close:g} 不低于 MA50 {m['ma50']:g}，趋势已破条件不成立，应作废"})
        elif status == "持仓中":
            tier = row.get("tier") or ""
            if "日线" not in tier:
                out.append({"issue": issue, "symbol": sym, "type": "check", "text": f"第{issue}期 {sym}：4小时档，程序暂不核对止损，请人工看"})
                continue
            if close is not None and ((short and close >= stop) or (not short and close <= stop)):
                out.append({"issue": issue, "symbol": sym, "type": "stop_out", "date": close_day.isoformat(), "price": close,
                            "text": f"第{issue}期 {sym}{side}：{close_day} 日线收盘 {close:g} 已触及止损 {stop:g}，应按收盘价了结"})
            else:
                out.append({"issue": issue, "symbol": sym, "type": "review",
                            "text": f"第{issue}期 {sym}{side}持仓：止损上移（2R保本、3R跟随、衰竭信号）程序暂未核算，请人工看"})
    return out


def fetch_bars(symbols, want=40):
    import 拉数据  # vendor module: futures klines with failover domains
    result = {}
    for sym in symbols:
        raw = 拉数据.klines(sym + "USDT", "1d", want)
        result[sym] = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in raw]
    return result


def latest_metrics(runtime):
    files = sorted(Path(runtime).glob("collection-*/每日汇总.json"), key=lambda p: p.stat().st_mtime)
    return (json.loads(files[-1].read_text()).get("指标") if files else None) or {}


def main(runtime="runtime", fetch=fetch_bars):
    path = Path(runtime) / "ledger.json"
    try:
        ledger = json.loads(path.read_text())
        if not ledger.get("ok"):
            return 0
        rows = ledger.get("active", [])
        symbols = sorted({r["symbol"] for r in rows if r.get("symbol")})
        proposals = reconcile(rows, fetch(symbols) if symbols else {}, latest_metrics(runtime),
                              datetime.now(BEIJING).date())
        ledger["proposals"] = proposals
        ledger["proposals_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(json.dumps(ledger, ensure_ascii=False))
        for item in proposals:
            print("::notice title=台账待更新::" + item["text"].replace("%", "%25"), flush=True)
        if not proposals:
            print("::notice title=台账核对::在场单子与行情一致，没有需要更新的", flush=True)
    except Exception as exc:  # noqa: BLE001 - never block the snapshot upload
        print("::warning title=台账核对未完成::" + str(exc)[:300].replace("%", "%25").replace("\n", " "), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "runtime"))
