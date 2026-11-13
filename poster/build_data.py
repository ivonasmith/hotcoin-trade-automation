"""Fill poster_gen's DATA / ROWS / CLOSED from the job payload. The generator itself is never edited.

Every trade number comes from the program's order plan (already formatted by the Worker) or the Notion ledger;
nothing is computed by a model. `verify` re-checks the rendered HTML before any image is sent.
"""
import re
from datetime import date

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def alt_leg_fields(a):
    """山寨整体涨跌腿（网站 alt_leg）的海报字段：只用百分比。网站没生成时海报显示「山寨整体数据未生成」。"""
    if not a or a.get("dir") not in ("up", "down"):
        return dict(aleg_dir=None)
    base = dict(aleg_dir=a["dir"], aleg_day=a["day"], aleg_start=a["start"], aleg_confirm=a["confirm"][5:],
                aleg_coins=a.get("coins"), aleg_prev=a.get("prev"))
    if a["dir"] == "down":
        return dict(base, aleg_drop=f"{a['drop_pct']:.1f}%", aleg_from_peak=f"{a['from_peak_pct']:+.1f}%".replace("-", "−"),
                    aleg_to_rev=f"+{a['to_reversal_pct']:.1f}%")
    return dict(base, aleg_gain=f"+{a['gain_pct']:.1f}%", aleg_dd=f"{a['max_dd_pct']:.1f}%")


def leg_fields(leg):
    """BTC 涨腿 / 跌腿的海报字段。数字原样取自网站涨跌腿数据（poster_facts），只做格式化。"""
    base = dict(leg_dir=leg.get("dir", "up"), leg_day=leg["day"], leg_start=leg["start"], leg_start_close=f"{leg['start_close']:,.2f}")
    if base["leg_dir"] == "down":
        return dict(base, leg_drop=leg["drop_pct"], leg_drop_txt=f"{leg['drop_pct']:.1f}%",
                    leg_low_close=f"{leg['low_close']:,.0f}", leg_low_date=leg["low_date"][5:],
                    leg_from_peak=f"{leg['from_peak_pct']:+.1f}%".replace("-", "−"),
                    leg_reversal=f"{leg['reversal_line']:,.2f}", leg_to_reversal=f"{leg['to_reversal_pct']:.1f}")
    return dict(base, leg_peak_pct=f"{leg['peak_pct']:+.1f}%", leg_peak_note=leg["peak_note_zh"], leg_peak_note_en=leg["peak_note_en"],
                leg_dd=f"{leg['dd']:.2f}%", leg_dd_date=leg["dd_date"], leg_past=max(leg["day"] - 89, 0),
                leg_badge_zh=leg.get("badge_zh") if "badge_zh" in leg else "多头只减不加",
                leg_badge_en=leg.get("badge_en") if "badge_en" in leg else "Longs: trim only")


def _trim(text):
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_price(value):
    """Same rules as cloud/order.mjs fmtPrice."""
    n = float(value)
    if n >= 1000:
        return f"{n:,.2f}"
    if n >= 1:
        return _trim(f"{n:.4f}")
    return _trim(f"{float(f'{n:.4g}'):.10f}")


def signed_pct(value):
    return f"{'+' if value >= 0 else '-'}{abs(value):.2f}%"


def size_label(size):
    if abs(size - 1) < 0.006:
        return "满仓", "Full size"
    if abs(size - 1 / 3) < 0.006:
        return "1/3仓", "1/3 size"
    if abs(size - 1 / 6) < 0.006:
        return "1/6仓", "1/6 size"
    return f"{size:.2f}仓", f"{size:.2f} size"


def tier_label(tier):
    return ("4小时", "4H") if "4" in (tier or "") and "日线" not in (tier or "") else ("日线", "Daily")


def money(value, plus=True):
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}" if plus else f"${abs(value):,.2f}"


def _row(sym, side, tier, status, size, entry, stop, target, price, moved=False):
    short = side == "做空"
    zh_size, en_size = size_label(size)
    tf_zh, tf_en = tier_label(tier)
    row = dict(sym=sym, side="空" if short else "多", tf_zh=tf_zh, tf_en=tf_en,
               state=status, state_en="Open" if status == "持仓中" else "Pending",
               size=zh_size, size_en=en_size, entry=fmt_price(entry), price=fmt_price(price) if price else "—",
               stop=fmt_price(stop), stop_sub=signed_pct((stop / entry - 1) * 100), stop_moved=bool(moved),
               target=fmt_price(target), target_sub=signed_pct((target / entry - 1) * 100))
    sort_key = 0.0
    if status == "持仓中" and price:
        gain = (entry - price) / entry if short else (price - entry) / entry
        pct = signed_pct(gain * 100)
        color = "var(--green)" if gain >= 0 else "var(--red)"
        row.update(pnl_zh=f'<span style="color:{color}">{pct}</span>', pnl_en=f'<span style="color:{color}">{pct}</span>',
                   pnl_sub_zh="币价口径", pnl_sub_en="by coin price", pnl_cls="green")
    else:
        dist = (entry / price - 1) * 100 if price else None
        row.update(pnl_zh="未成交", pnl_en="Unfilled", pnl_cls="gray",
                   pnl_sub_zh=f"距现价 {signed_pct(dist)}" if dist is not None else "",
                   pnl_sub_en=f"{signed_pct(dist)} away" if dist is not None else "")
        sort_key = abs(dist) if dist is not None else 1e9
    return row, (0 if status == "持仓中" else 1, sort_key)


def build(payload):
    day = payload["day"]
    today = date.fromisoformat(day)
    ledger = payload.get("ledger") or {}
    facts = payload.get("facts") or {}
    prices = payload.get("prices") or {}
    order = payload.get("order")
    formal = payload["formal"]
    issue = str(payload["issue"])

    rows = []
    seen = set()
    fills = {(str(f.get("issue")), f.get("symbol")) for f in (ledger.get("fills") or [])}
    for item in ledger.get("active") or []:
        if item.get("status") not in ("挂单中", "持仓中") or not all(item.get(k) for k in ("entry", "stop", "target")):
            continue
        seen.add((str(item.get("issue")), item.get("symbol")))
        status = item["status"]
        if status == "挂单中" and (str(item.get("issue")), item.get("symbol")) in fills:
            status = "持仓中"   # 台账程序已判定盘中触及进场，但写入要等终审之后
        # 持仓中的单子，止损用台账日志里最新的「止损上移」价（stop_now）；登记的老止损一栏从不修改。挂单没成交，不存在上移。
        moved_to = item.get("stop_now") if status == "持仓中" and item.get("status") == "持仓中" else None
        moved = bool(moved_to) and moved_to != item["stop"]
        rows.append(_row(item["symbol"], item.get("side", "做空"), item.get("tier"), status,
                         item.get("size") or 1.0, item["entry"], moved_to if moved else item["stop"], item["target"],
                         prices.get(item["symbol"]), moved=moved))
    if order and (issue, order["symbol"]) not in seen:
        rows.append(_row(order["symbol"], "做空", "慢档日线", "挂单中", order["size"], order["entry"], order["stop"],
                         order["target"], prices.get(order["symbol"])))
    rows.sort(key=lambda pair: pair[1])
    rows = [pair[0] for pair in rows]
    opened = sum(1 for r in rows if r["state"] == "持仓中")
    pending = len(rows) - opened

    leg, alt = facts.get("leg") or {}, facts.get("alt") or {}
    if not leg:
        raise ValueError("缺少 BTC 涨跌腿数据（poster_facts），不出图")
    cum, equity = ledger.get("cumulative_pnl_usd"), ledger.get("equity_usd")
    if cum is None or equity is None:
        raise ValueError("缺少台账账户数据，不出图")
    mmdd = day[5:]
    new_zh = f"新增{order['symbol']}空单挂单1张" if order else "今日无新单"
    new_en = f"1 new {order['symbol']} short order pending" if order else "no new order"
    data = dict(
        issue=issue, date=day,
        **leg_fields(leg),
        **alt_leg_fields(facts.get("alt_leg")),
        acct_closed=int((ledger.get("counts") or {}).get("closed", 0)),
        acct_note=f"更新至第{issue}期·{mmdd}（{new_zh}）", acct_note_en=f"updated to Issue {issue} · {mmdd} ({new_en})",
        acct_cum=money(cum), acct_cur=money(equity, plus=False), acct_pct=f"{cum / 10000 * 100:+.2f}%",
        acct_open=opened, acct_pending=pending,
        badge_today_zh="今日新增1单" if order else "今日无新单", badge_today_en="1 new order" if order else "No new order",
        today_zh=formal["zh"].strip(), today_en=formal["en"].strip(),
        note_zh="全部在场持仓与挂单都在上表——完整历史清单见交易记录追踪表。做空单的止损只下移、不上移。",
        note_en="All open positions and pending orders are in the table above — full history in the trade log. For short orders the stop only moves down, never up.",
    )
    closed = [(c["symbol"], str(c["issue"]), c["pct"], c["usd"]) for c in (ledger.get("closed_recent") or [])[:6]]
    if len(closed) < 6:
        raise ValueError("台账里读到的已了结单子不足6张，不出图")
    return data, rows, closed


def _text(html):
    return re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", "", html, flags=re.S))


def verify(htmls, payload):
    """htmls: {(lang, dev): html}. Returns a list of problems; empty means fine."""
    problems = []
    order = payload.get("order")
    for (lang, dev), html in htmls.items():
        text = _text(html)
        if lang == "en" and CJK.search(text):
            problems.append(f"英文{dev}版出现中文字符：{CJK.search(text).group(0)}")
        if order:
            for key in ("entry", "stop", "target"):
                if fmt_price(order[key]) not in text:
                    problems.append(f"{lang}/{dev} 缺少订单{key} {fmt_price(order[key])}")
        if f"{payload['issue']}" not in text:
            problems.append(f"{lang}/{dev} 缺少期号")
        # 涨跌腿数字必须原样出现在图上（数据来自网站，海报只格式化）
        leg, al = (payload.get("facts") or {}).get("leg") or {}, (payload.get("facts") or {}).get("alt_leg") or {}
        if leg.get("dir") == "down" and f"{leg['reversal_line']:,.2f}" not in text:
            problems.append(f"{lang}/{dev} BTC 跌腿缺少反弹确认线 {leg['reversal_line']:,.2f}")
        if leg.get("day") is not None and str(leg["day"]) not in text:
            problems.append(f"{lang}/{dev} BTC 涨跌腿缺少天数 {leg['day']}")
        if al.get("dir") == "down" and f"{al['drop_pct']:.1f}%" not in text:
            problems.append(f"{lang}/{dev} 山寨整体缺少跌幅 {al['drop_pct']:.1f}%")
    return problems
