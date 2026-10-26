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


def sig6(value):
    return float(f"{value:.6g}")


def stop_move(row, done, short, entry, stop, live):
    """The two mechanical rules registered with every order: 2R reached on a daily close -> stop to the entry price;
    3R reached -> stop follows MA20 (never beyond the entry price). The stop only moves toward profit, and never to a
    level the last close has already crossed. Judgement moves (exhaustion signals, resistance bands) stay with a human.
    Returns (new_stop, reason) or None."""
    filled = _to_date(row.get("filled")) or _to_date(row.get("day"))
    risk = (stop - entry) if short else (entry - stop)   # R from the PUBLISHED stop, which never changes
    closes = [b[4] for b in done if filled and utc_day(b[0]) >= filled]
    if risk <= 0 or not closes or len(done) < 20:
        return None
    best = min(closes) if short else max(closes)
    reached = (entry - best) / risk if short else (best - entry) / risk
    if reached < 2:
        return None
    ma20 = sum(b[4] for b in done[-20:]) / 20
    if reached >= 3:
        cand, why = (min(entry, ma20) if short else max(entry, ma20)), f"已到3R（最好收盘{reached:.2f}R），止损跟随MA20 {ma20:g}"
    else:
        cand, why = entry, f"已到2R（最好收盘{reached:.2f}R），止损移到进场价"
    cand = sig6(cand)
    last = done[-1][4]
    better = cand < live - 1e-9 * abs(live) if short else cand > live + 1e-9 * abs(live)
    still_open = cand > last if short else cand < last
    return (cand, why) if better and still_open else None


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
            live = row.get("stop_now") or stop      # the moved stop booked in the log, else the published one
            if close is not None and ((short and close >= live) or (not short and close <= live)):
                out.append({"issue": issue, "symbol": sym, "type": "stop_out", "date": close_day.isoformat(), "price": close,
                            "text": f"第{issue}期 {sym}{side}：{close_day} 日线收盘 {close:g} 已触及止损 {live:g}，应按收盘价了结"})
                continue
            move = stop_move(row, done, short, entry, stop, live)
            if move:
                out.append({"issue": issue, "symbol": sym, "type": "stop_move", "date": close_day.isoformat(), "price": move[0],
                            "text": f"第{issue}期 {sym}{side}：{close_day} 日线收盘 {close:g}，{move[1]}，止损 {live:g} → {move[0]:g}"})
            else:
                out.append({"issue": issue, "symbol": sym, "type": "review",
                            "text": f"第{issue}期 {sym}{side}持仓：衰竭信号等酌情上移程序暂未核算，请人工看"})
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
