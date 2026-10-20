"""Daily poster facts: BTC leg day / peak / drawdown and the alt window day, from daily closes.

The leg start and alt D0 are rule state kept in poster/poster_state.json; everything else is computed here
from completed daily bars, never by a model. Output: runtime/poster_facts.json (uploaded with the snapshot).
Never fails the workflow: on any problem it writes {"ok": false, "error": ...}.
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE.parent / "poster" / "poster_state.json"
BEIJING = timezone(timedelta(hours=8))
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def utc_day(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).date()


def compute(state, bars, today):
    """bars: [(open_ts_ms, o, h, l, c, v)...] oldest first; the last bar may still be forming."""
    start = date.fromisoformat(state["leg_start"])
    done = [b for b in bars if utc_day(b[0]) < today]  # completed days only
    closes = [(utc_day(b[0]), b[4]) for b in done if utc_day(b[0]) >= start]
    if not closes or closes[0][0] != start:
        raise ValueError("日线数据没有覆盖涨腿起点，无法计算涨腿统计")
    if abs(closes[0][1] / state["leg_start_close"] - 1) > 0.002:
        raise ValueError(f"起点收盘价与登记值不符：行情 {closes[0][1]:g}，登记 {state['leg_start_close']:g}")
    peak_day, peak = max(closes, key=lambda item: item[1])
    run, dd, dd_day = closes[0][1], 0.0, closes[0][0]
    for day, close in closes:
        run = max(run, close)
        if (run - close) / run > dd:
            dd, dd_day = (run - close) / run, day
    last_day = closes[-1][0]
    refreshed = peak_day == last_day
    note_zh = f"{peak_day:%m-%d}最高收盘{peak:,.0f}，" + ("今日刷新" if refreshed else "未刷新")
    note_en = f"{MONTHS[peak_day.month - 1]} {peak_day.day} peak close {peak:,.0f}, " + ("new high" if refreshed else "not exceeded")
    d0 = date.fromisoformat(state["alt_d0"])
    cleared = date.fromisoformat(state["alt_cleared"])
    return {
        "leg": {"day": (today - start).days, "start": state["leg_start"], "start_close": state["leg_start_close"],
                "peak_pct": (peak / state["leg_start_close"] - 1) * 100, "peak_note_zh": note_zh, "peak_note_en": note_en,
                "dd": dd * 100, "dd_date": f"{dd_day:%m-%d}"},
        "alt": {"day": (today - d0).days, "d0": f"{d0:%m-%d}", "d0_close": state["alt_d0_close"],
                "cleared": f"{cleared:%m-%d}", "top3": state["alt_top3"], "top3_en": state["alt_top3_en"]},
    }


def fetch_bars(want=200):
    sys.path.insert(0, str(HERE / "vendor"))
    import 拉数据  # vendor module: futures klines with failover domains
    return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
            for k in 拉数据.klines("BTCUSDT", "1d", want)]


def main(runtime="runtime", fetch=fetch_bars):
    target = Path(runtime) / "poster_facts.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE.read_text())
        facts = {"ok": True, **compute(state, fetch(), datetime.now(BEIJING).date())}
        print(f"::notice title=海报数据::涨腿第{facts['leg']['day']}天，山寨窗口D0+{facts['alt']['day']}", flush=True)
    except Exception as exc:  # noqa: BLE001 - never block the snapshot upload
        facts = {"ok": False, "error": str(exc)[:300]}
        print("::warning title=海报数据未生成::" + str(exc)[:300].replace("%", "%25").replace("\n", " "), flush=True)
    target.write_text(json.dumps(facts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2])))
