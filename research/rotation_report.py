#!/usr/bin/env python3
"""龙头 / 轮动研究：某个区间里哪些币先涨、哪些后涨，谁先站上 200 日 / 365 日均线，周线日线 MACD 的状态。

数据：币安 U 本位永续合约日线（北京时间 08:00 开盘，即 UTC 日线）；币种名单 = CoinGecko 当前市值榜里
剔除稳定币/包装币/黄金币后、币安有永续合约的前 N 名（榜单取不到时退回用币安 24h 成交额排序）。
只用标准库。输出：rotation_report.html（给人看）和 rotation_data.json（全部数字）。

用法：python3 research/rotation_report.py --out 输出目录 [--top 30] [--start 2026-07-01] [--end 2026-10-31]
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
import monitor  # noqa: E402

CG_URL = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"
STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "USDS", "USDE", "USD1", "USDG", "PYUSD", "RLUSD", "USDY", "USYC", "BUIDL", "USDF", "USDD",
                  "BFUSD", "USDGO", "OUSD", "FDUSD", "TUSD", "USDP", "GUSD", "FRAX", "LUSD", "SUSDS", "SUSDE", "USDTB", "USDX", "EURC"}
SKIP_NAME = re.compile(r"(wrapped|staked|bridged|restaked|liquid staking|\bgold\b|tether gold|pax gold|treasury|heloc|\bwbtc\b|\bweth\b|usd)", re.I)
UP20, UP50, UP100 = 0.2, 0.5, 1.0


# ---------- 取数 ----------
def fetch_json(url, getter, tries=3):
    last = None
    for attempt in range(tries):
        try:
            return getter(url)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "429" in str(exc):
                time.sleep(20 * (attempt + 1))
            elif attempt + 1 < tries:
                time.sleep(2)
    raise RuntimeError(f"拉取失败：{url[:80]}：{last}")


def perp_bases(getter):
    for base in monitor.FAPI_BASES:
        try:
            info = getter(f"{base}/fapi/v1/exchangeInfo")
            return {s["baseAsset"] for s in info["symbols"]
                    if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"}
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("拿不到币安合约列表（fapi/exchangeInfo）")


def fetch_daily(contract, getter, limit=1500):
    last = None
    for base in monitor.FAPI_BASES:
        try:
            rows = getter(f"{base}/fapi/v1/klines?" + urllib.parse.urlencode({"symbol": contract, "interval": "1d", "limit": limit}))
            bars = monitor.parse_klines(rows)
            for bar, row in zip(bars, rows):
                bar["qv"] = float(row[7]) if len(row) > 7 else None   # 当天成交额（USDT）
            return bars
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"{contract} 拉不到日线：{last}")


def is_stable_like(row):
    price, change = row.get("current_price") or 0, abs(row.get("price_change_percentage_24h") or 0)
    return 0.95 < price < 1.05 and change < 0.6 and (row.get("ath") or 0) < 1.6


def universe(top_n, getter, bases, warnings):
    """返回 [{symbol, name, rank, mcap, contract, cg_price}]，已按市值从大到小排好、剔除稳定币等。"""
    rows = []
    try:
        for row in fetch_json(CG_URL, getter):
            sym = str(row["symbol"]).upper()
            if sym in STABLE_SYMBOLS or SKIP_NAME.search(row.get("name") or "") or is_stable_like(row):
                continue
            contract = sym if sym in bases else ("1000" + sym if "1000" + sym in bases else None)
            if contract:
                rows.append({"symbol": sym, "name": row.get("name"), "mcap": row.get("market_cap"), "contract": contract,
                             "cg_price": row.get("current_price"), "cg_rank": row.get("market_cap_rank"), "cg_vol24": row.get("total_volume")})
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"市值榜取不到（{str(exc)[:80]}），改用币安 24h 成交额排序，口径不是市值")
        tickers = fetch_json(f"{monitor.FAPI_BASES[0]}/fapi/v1/ticker/24hr", getter)
        for row in sorted(tickers, key=lambda r: -float(r.get("quoteVolume") or 0)):
            name = row["symbol"]
            if not name.endswith("USDT"):
                continue
            base = name[:-4]
            sym = base[4:] if base.startswith("1000") else base
            if sym in STABLE_SYMBOLS or sym in {r["symbol"] for r in rows} or base not in bases:
                continue
            rows.append({"symbol": sym, "name": sym, "mcap": None, "contract": base, "cg_price": None, "cg_rank": None, "cg_vol24": None})
    return rows


# ---------- 指标 ----------
def day_of(bar):
    return datetime.fromtimestamp(bar["open_time"] / 1000, tz=timezone.utc).date()


def sma(values, period):
    out, total = [None] * len(values), 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values, period):
    k, out, prev = 2 / (period + 1), [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    dif = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    dea = ema(dif, signal)
    return dif, dea, [d - e for d, e in zip(dif, dea)]


def weekly(dates, closes):
    """按 ISO 周（周一到周日，UTC）取每周最后一个收盘；最后一周可能没走完。"""
    out_d, out_c = [], []
    for d, c in zip(dates, closes):
        key = d.isocalendar()[:2]
        if out_d and out_d[-1][0] == key:
            out_d[-1] = (key, d)
            out_c[-1] = c
        else:
            out_d.append((key, d))
            out_c.append(c)
    return [d for _, d in out_d], out_c


def first_cross(dates, a, b, lo, hi):
    """[lo, hi] 内第一次 a 从不高于 b 变成高于 b 的日期（a、b 都有值才算）。"""
    for i in range(max(lo, 1), hi + 1):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        if a[i] > b[i] and a[i - 1] <= b[i - 1]:
            return dates[i]
    return None


def iso(d):
    return d.isoformat() if d else None


def analyze(sym, bars, start, end, min_turnover=5e6):
    dates = [day_of(b) for b in bars]
    closes = [b["close"] for b in bars]
    if start <= dates[0]:
        return {"symbol": sym, "skipped": f"数据从 {dates[0]} 才开始，起点前没有基准价"}
    lo = max(i for i, d in enumerate(dates) if d < start)          # 起点前一天 = 基准日
    hi = max(i for i, d in enumerate(dates) if d <= end)
    if hi <= lo:
        return {"symbol": sym, "skipped": "区间内没有日线数据"}
    base = closes[lo]
    res = {"symbol": sym, "base_date": iso(dates[lo]), "base_close": base, "last_date": iso(dates[-1]), "last_close": closes[-1]}
    window = range(lo + 1, hi + 1)
    res["ret_end"] = closes[hi] / base - 1
    res["ret_now"] = closes[-1] / base - 1
    low_i = min(window, key=lambda i: closes[i])
    peak_i = max(window, key=lambda i: closes[i])
    res.update(low_date=iso(dates[low_i]), low_close=closes[low_i], peak_date=iso(dates[peak_i]), peak_close=closes[peak_i],
               peak_ret=closes[peak_i] / base - 1, from_peak_now=closes[-1] / closes[peak_i] - 1)
    # 启动日：相对“到当天为止的区间最低收盘”首次涨超 20% / 50% / 100%
    run_min, hits = float("inf"), {}
    for i in window:
        run_min = min(run_min, closes[i])
        for tag, level in (("up20", UP20), ("up50", UP50), ("up100", UP100)):
            if tag not in hits and closes[i] / run_min - 1 >= level:
                hits[tag] = dates[i]
    res.update({k: iso(hits.get(k)) for k in ("up20", "up50", "up100")})
    res["from_low_now"] = closes[-1] / closes[low_i] - 1
    late, rm = None, run_min
    if "up20" not in hits:   # 区间内没启动：看区间结束之后到现在有没有涨起来
        for i in range(hi + 1, len(closes)):
            rm = min(rm, closes[i])
            if closes[i] / rm - 1 >= UP20:
                late = dates[i]
                break
    res["up20_late"] = iso(late)
    res["max_rally"] = max((closes[i] / min(closes[lo + 1:i + 1]) - 1 for i in window), default=0)
    # 成交活跃度：窗口内每天的币安永续成交额（USDT）
    qv = sorted(b["qv"] for b in bars[lo + 1:hi + 1] if b.get("qv") is not None)
    last30 = [b["qv"] for b in bars[-30:] if b.get("qv") is not None]
    res["turnover"] = {"median": qv[len(qv) // 2] if qv else None, "min": qv[0] if qv else None,
                       "last30_avg": sum(last30) / len(last30) if last30 else None}
    # 均线
    for period in (200, 365):
        ma = sma(closes, period)
        tag = f"ma{period}"
        if ma[hi] is None:
            res[tag] = {"available": False}
            continue
        above_base = ma[lo] is not None and closes[lo] > ma[lo]
        cross = first_cross(dates, closes, ma, lo + 1, hi)
        eff = dates[lo] if above_base else cross
        days = [closes[i] > ma[i] for i in window if ma[i] is not None]
        slope = (ma[-1] / ma[-21] - 1) if len(ma) > 21 and ma[-21] else None
        res[tag] = {"available": True, "above_at_base": above_base if ma[lo] is not None else None, "cross_date": iso(cross),
                    "effective_date": iso(eff), "above_end": closes[hi] > ma[hi], "above_now": closes[-1] > ma[-1],
                    "dist_now": closes[-1] / ma[-1] - 1, "pct_days_above": sum(days) / len(days) if days else None,
                    "slope_20d": slope, "ma_now": ma[-1]}
    both = []
    if res["ma365"].get("available"):
        s200, s365 = sma(closes, 200), sma(closes, 365)
        both = [i for i in window if s365[i] is not None and closes[i] > s200[i] and closes[i] > s365[i]]
    res["both_date"] = iso(dates[both[0]]) if both else None
    # 日线 MACD
    dif, dea, hist = macd(closes)
    res["macd_d"] = {"dif_now": dif[-1], "hist_now": hist[-1], "zero_cross": iso(first_cross(dates, dif, [0.0] * len(dif), lo + 1, hi)),
                     "gold_cross": iso(first_cross(dates, dif, dea, lo + 1, hi)), "dif_above_zero_now": dif[-1] > 0}
    # 周线 MACD
    wd, wc = weekly(dates, closes)
    wdif, wdea, whist = macd(wc)
    wlo = max((i for i, d in enumerate(wd) if d < start), default=0)
    whi = max(i for i, d in enumerate(wd) if d <= end + timedelta(days=6))
    res["macd_w"] = {"dif_now": wdif[-1], "dea_now": wdea[-1], "hist_now": whist[-1], "hist_prev": whist[-2] if len(whist) > 1 else None,
                     "gold_cross": iso(first_cross(wd, wdif, wdea, wlo + 1, whi)), "zero_cross": iso(first_cross(wd, wdif, [0.0] * len(wdif), wlo + 1, whi)),
                     "dif_above_zero_now": wdif[-1] > 0, "hist_up_now": len(whist) > 1 and whist[-1] > whist[-2],
                     "dif_above_zero_base": wdif[wlo] > 0}
    # 路径（按周，归一到基准日=100）
    res["path"] = [[iso(d), round(c / base * 100, 2)] for i, (d, c) in enumerate(zip(wd, wc)) if wlo <= i <= whi]
    res["structure"] = structure(res)
    med = res["turnover"]["median"]
    res["low_activity"] = med is not None and med < min_turnover
    return res


def structure(res):
    m2, m3, w = res["ma200"], res["ma365"], res["macd_w"]
    if not m2.get("available"):
        return "数据不足（不满200日）"
    above2 = m2["above_now"]
    above3 = m3.get("above_now") if m3.get("available") else None
    wz = w["dif_above_zero_now"]
    if above2 and above3 is not False and wz:
        return "强势多头（站上MA200" + ("/MA365" if above3 else "") + "，周线MACD在零轴上）"
    if above2:
        return "修复中（站上MA200，" + ("MA365下方" if above3 is False else "") + ("周线MACD在零轴上" if wz else "周线MACD仍在零轴下") + "）"
    if w["hist_up_now"]:
        return "筑底（在MA200下方，周线动能回升）"
    return "弱势（在MA200下方，周线动能向下）"


# ---------- 排序 / 综合 ----------
def pct_rank(values, earlier_better=False, higher_better=True):
    """每个元素在有值的元素里的位置分，最好 = 1，最差 = 0；没有值 = 0。"""
    have = sorted((v for v in values if v is not None), reverse=higher_better and not earlier_better)
    n = len(have)
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
        else:
            out.append(1.0 if n == 1 else 1 - have.index(v) / (n - 1))
    return out


def macd_score(w):
    return 1.0 if w["dif_above_zero_now"] and w["hist_now"] > 0 else 0.6 if w["dif_above_zero_now"] else 0.4 if w["hist_now"] > 0 else 0.0


def rank_all(coins):
    live = [c for c in coins if "skipped" not in c]
    s_start = pct_rank([c["up20"] for c in live], earlier_better=True)
    s_ret = pct_rank([c["ret_end"] for c in live])
    def eff(c, tag):
        return c[tag].get("effective_date") if c[tag].get("available") else None
    s_ma2 = pct_rank([eff(c, "ma200") for c in live], earlier_better=True)
    s_ma3 = pct_rank([eff(c, "ma365") for c in live], earlier_better=True)
    for c, a, b, d, e in zip(live, s_start, s_ret, s_ma2, s_ma3):
        ma = (d + e) / 2 if c["ma365"].get("available") else d
        c["scores"] = {"start": round(a, 3), "ret": round(b, 3), "ma": round(ma, 3), "macd": macd_score(c["macd_w"])}
        c["composite"] = round(0.3 * a + 0.3 * b + 0.2 * ma + 0.2 * c["scores"]["macd"], 3)
    return sorted(live, key=lambda c: -c["composite"])


# ---------- 报告 ----------
def pc(v, digits=1):
    return "—" if v is None else f"{v * 100:+.{digits}f}%"


def dt(s):
    return "—" if not s else s[5:]


def yn(v):
    return "—" if v is None else ("是" if v else "否")


def table(headers, rows):
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(x))}</td>" for x in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


COLORS = ["#d95f02", "#1b9e77", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#386cb0", "#a6761d"]


def chart(coins, btc, start, end):
    width, height, left, bottom = 860, 340, 46, 28
    series = [(c["symbol"], c["path"]) for c in coins] + ([("BTC", btc["path"])] if btc else [])
    pts = [p for _, path in series for p in path]
    if not pts:
        return ""
    d0, d1 = date.fromisoformat(min(p[0] for p in pts)), date.fromisoformat(max(p[0] for p in pts))
    vmax, vmin = max(p[1] for p in pts), min(p[1] for p in pts)
    span = max((d1 - d0).days, 1)
    def x(d):
        return left + (date.fromisoformat(d) - d0).days / span * (width - left - 10)
    def y(v):
        return 10 + (vmax - v) / (vmax - vmin or 1) * (height - bottom - 10)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="区间走势（基准日=100）">']
    for tick in range(5):
        v = vmin + (vmax - vmin) * tick / 4
        parts.append(f'<line x1="{left}" x2="{width - 10}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/><text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.0f}</text>')
    for mark in (start, end):
        if d0 <= mark <= d1:
            parts.append(f'<line x1="{x(mark.isoformat()):.1f}" x2="{x(mark.isoformat()):.1f}" y1="10" y2="{height - bottom}" class="mark"/>')
    for i, (name, path) in enumerate(series):
        color = "#888" if name == "BTC" else COLORS[i % len(COLORS)]
        dash = ' stroke-dasharray="5 4"' if name == "BTC" else ""
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2"{dash} points="' + " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in path) + '"/>')
        parts.append(f'<text x="{x(path[-1][0]) - 2:.1f}" y="{y(path[-1][1]) - 5:.1f}" text-anchor="end" fill="{color}">{html.escape(name)}</text>')
    parts.append(f'<text x="{left}" y="{height - 8}">{d0}</text><text x="{width - 10}" y="{height - 8}" text-anchor="end">{d1}</text></svg>')
    return "".join(parts)


def bucket_lines(ranked):
    """按市值榜名次分档，看大币和小币谁先启动、涨幅中位数。"""
    out = []
    for lo, hi, name in ((1, 30, "前30名"), (31, 100, "31–100名"), (101, 10 ** 6, "101名以后")):
        group = [c for c in ranked if c.get("cg_rank") and lo <= c["cg_rank"] <= hi]
        if not group:
            continue
        days = sorted(date.fromisoformat(c["up20"]).toordinal() for c in group if c["up20"])
        rets = sorted(c["ret_end"] for c in group)
        mid = date.fromordinal(days[len(days) // 2]).isoformat()[5:] if days else "—"
        out.append(f"{name}（{len(group)}个）：+20%首日中位数 {mid}，{len(days)}/{len(group)} 个涨到过 +20%；区间收益中位数 {pc(rets[len(rets) // 2], 0)}。")
    return out


def money(v):
    return "—" if v is None else (f"{v / 1e8:.1f}亿" if v >= 1e8 else f"{v / 1e4:,.0f}万")


def activity_lines(ranked, threshold):
    low = [c for c in ranked if c["low_activity"]]
    act = [c for c in ranked if not c["low_activity"]]
    med = lambda cs, key: sorted(c[key] for c in cs)[len(cs) // 2] if cs else None
    out = [f"成交活跃度：窗口内币安永续日成交额中位数低于 {money(threshold)} 美元的“低活跃”币 {len(low)} 个"
           + (f"（{'、'.join(c['symbol'] for c in low[:25])}{'…' if len(low) > 25 else ''}）" if low else "") + "。"]
    if low and act:
        out.append(f"低活跃组区间收益中位数 {pc(med(low, 'ret_end'), 0)}，高点前最大涨幅中位数 {pc(med(low, 'max_rally'), 0)}；活跃组分别是 {pc(med(act, 'ret_end'), 0)}、{pc(med(act, 'max_rally'), 0)}。")
    return out


def launch_queue(ranked, btc):
    """启动观察队列：窗口内按 +20% 首日排队并分批；窗口后才启动的；至今没启动的。"""
    started = sorted([c for c in ranked if c["up20"]], key=lambda c: (c["up20"], c["symbol"]))
    n = len(started)
    for i, c in enumerate(started):
        c["batch"] = 1 + min(3, i * 4 // max(n, 1))
    late = sorted([c for c in ranked if not c["up20"] and c["up20_late"]], key=lambda c: (c["up20_late"], c["symbol"]))
    waiting = sorted([c for c in ranked if not c["up20"] and not c["up20_late"]], key=lambda c: -(c["ma200"].get("dist_now") if c["ma200"].get("available") else -9))
    return started, late, waiting


def build_html(meta, ranked, skipped, btc):
    start, end = date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"])
    by_start = sorted([c for c in ranked if c["up20"]], key=lambda c: (c["up20"], c["symbol"]))
    never = [c["symbol"] for c in ranked if not c["up20"]]
    n = len(ranked)
    above_base = sum(1 for c in ranked if c["ma200"].get("above_at_base"))
    above_now = sum(1 for c in ranked if c["ma200"].get("above_now"))
    batch = lambda cs: "、".join(c["symbol"] for c in cs) or "—"
    third = max(1, len(by_start) // 3)
    top = ranked[:5]
    summary = [
        f"区间 {start} → {end}（UTC 日线，基准 = {start} 前一天收盘），分析 {n} 个币" + (f"，另有 {len(skipped)} 个因数据不足未纳入" if skipped else "") + "。",
        f"BTC 区间收益 {pc(btc['ret_end']) if btc else '—'}；站上 MA200 的币：起点 {above_base}/{n} → 现在 {above_now}/{n}。",
        f"最先启动（相对区间低点首次 +20%）：{batch(by_start[:third])}；中段：{batch(by_start[third:2 * third])}；最后：{batch(by_start[2 * third:])}"
        + (f"；区间内没涨到 +20%：{'、'.join(never)}" if never else "") + "。",
        *bucket_lines(ranked),
        *activity_lines(ranked, meta["min_turnover"]),
        "综合分前 5（按下表公式，只是排序，不是买卖建议）：" + "、".join(f"{c['symbol']}({c['composite']:.2f})" for c in top) + "。",
    ]
    started, late, waiting = launch_queue(ranked, btc)
    btc_day = date.fromisoformat(btc["up20"]) if btc and btc["up20"] else None
    def vs_btc(day):
        return "—" if not (btc_day and day) else f"{(date.fromisoformat(day) - btc_day).days:+d}天"
    q1 = table(["序号", "币", "批次", "区间低点日", "启动日(+20%)", "相对BTC启动", "+50%首日", "市值榜名次", "区间收益", "至今收益", "现价距MA200", "周线MACD", "价格结构", "活跃度"],
               [[i + 1, c["symbol"], f"第{c['batch']}批", dt(c["low_date"]), dt(c["up20"]), vs_btc(c["up20"]), dt(c["up50"]), c.get("cg_rank") or "—",
                 pc(c["ret_end"], 0), pc(c["ret_now"], 0), pc(c["ma200"].get("dist_now")),
                 ("零轴上" if c["macd_w"]["dif_above_zero_now"] else "零轴下") + ("·回升" if c["macd_w"]["hist_up_now"] else "·走弱"),
                 c["structure"].split("（")[0], "低活跃" if c["low_activity"] else "正常"] for i, c in enumerate(started)])
    q2 = table(["币", "市值榜名次", "窗口后启动日(+20%)", "距区间低点", "现价距MA200", "周线MACD", "价格结构", "活跃度"],
               [[c["symbol"], c.get("cg_rank") or "—", dt(c["up20_late"]), pc(c["from_low_now"], 0), pc(c["ma200"].get("dist_now")),
                 ("零轴上" if c["macd_w"]["dif_above_zero_now"] else "零轴下") + ("·回升" if c["macd_w"]["hist_up_now"] else "·走弱"),
                 c["structure"].split("（")[0], "低活跃" if c["low_activity"] else "正常"] for c in late])
    q3 = table(["币", "市值榜名次", "距区间低点", "现价距MA200", "现价距MA365", "周线MACD", "价格结构", "活跃度"],
               [[c["symbol"], c.get("cg_rank") or "—", pc(c["from_low_now"], 0), pc(c["ma200"].get("dist_now")),
                 pc(c["ma365"].get("dist_now")) if c["ma365"].get("available") else "—",
                 ("零轴上" if c["macd_w"]["dif_above_zero_now"] else "零轴下") + ("·回升" if c["macd_w"]["hist_up_now"] else "·走弱"),
                 c["structure"].split("（")[0], "低活跃" if c["low_activity"] else "正常"] for c in waiting])
    queue_html = ("<h2>0. 启动观察队列（谁先谁后）</h2><p class='note'>排队依据：相对到当天为止的区间最低收盘，第一次涨超 +20% 的日期；按先后平均分成四批。</p>"
                  f"<h3>已启动（区间内，共 {len(started)} 个）</h3>" + (q1 if started else "<p class='note'>无。</p>") +
                  f"<h3>区间结束后才启动（共 {len(late)} 个）</h3>" + (q2 if late else "<p class='note'>无。</p>") +
                  f"<h3>至今没启动（共 {len(waiting)} 个；按现价距MA200由高到低，接近或已站上的靠前）</h3>" + (q3 if waiting else "<p class='note'>无。</p>"))
    for c in ranked:
        c["queue"] = ("第%d批" % c["batch"]) if c.get("batch") else ("区间后启动" if c["up20_late"] else "未启动")
    t1 = table(["币", "市值榜名次", "区间低点日", "+20%首日", "+50%首日", "+100%首日", "高点日", "低点→高点最大涨幅", "区间收益", "至今收益", "相对BTC", "高点至今", "窗口日均成交额(中位)", "活跃度"],
               [[c["symbol"], c.get("cg_rank") or "—", dt(c["low_date"]), dt(c["up20"]), dt(c["up50"]), dt(c["up100"]), dt(c["peak_date"]), pc(c["max_rally"], 0),
                 pc(c["ret_end"]), pc(c["ret_now"]), pc(c["ret_end"] - btc["ret_end"]) if btc else "—", pc(c["from_peak_now"]),
                 money(c["turnover"]["median"]), "低活跃" if c["low_activity"] else "正常"]
                for c in sorted(ranked, key=lambda c: (c["up20"] or "9999", c["symbol"]))])
    t2 = table(["币", "起点在MA200上", "站上MA200日", "站上MA365日", "两线都站上", "区间内收盘在MA200上占比", "现价距MA200", "现价距MA365", "MA200 20日斜率"],
               [[c["symbol"], yn(c["ma200"].get("above_at_base")), dt(c["ma200"].get("cross_date")) if c["ma200"].get("available") else "不足",
                 dt(c["ma365"].get("cross_date")) if c["ma365"].get("available") else "不足", dt(c["both_date"]),
                 f"{c['ma200']['pct_days_above'] * 100:.0f}%" if c["ma200"].get("pct_days_above") is not None else "—",
                 pc(c["ma200"].get("dist_now")), pc(c["ma365"].get("dist_now")) if c["ma365"].get("available") else "—", pc(c["ma200"].get("slope_20d"))]
                for c in sorted(ranked, key=lambda c: (c["ma200"].get("effective_date") or "9999", c["symbol"]))])
    t3 = table(["币", "周线金叉日", "周线DIF上穿零轴日", "周线DIF在零轴上", "周线柱子", "柱子变化", "日线DIF上穿零轴日", "日线DIF在零轴上"],
               [[c["symbol"], dt(c["macd_w"]["gold_cross"]), dt(c["macd_w"]["zero_cross"]), yn(c["macd_w"]["dif_above_zero_now"]),
                 "红(>0)" if c["macd_w"]["hist_now"] > 0 else "绿(<0)", "回升" if c["macd_w"]["hist_up_now"] else "走弱",
                 dt(c["macd_d"]["zero_cross"]), yn(c["macd_d"]["dif_above_zero_now"])] for c in ranked])
    t4 = table(["排名", "币", "综合分", "启动早晚", "区间涨幅", "均线", "周线MACD", "价格结构", "活跃度"],
               [[i + 1, c["symbol"], f"{c['composite']:.2f}", f"{c['scores']['start']:.2f}", f"{c['scores']['ret']:.2f}", f"{c['scores']['ma']:.2f}",
                 f"{c['scores']['macd']:.1f}", c["structure"], "低活跃" if c["low_activity"] else "正常"] for i, c in enumerate(ranked)])
    has_low = any(c["low_activity"] or c.get("low_now") for c in ranked)
    t5 = table(["币", "市值榜名次", "窗口日均成交额(中位)", "窗口最低日成交额", "近30日日均", "全市场24h成交额(CoinGecko)", "区间收益", "高点前最大涨幅", "+20%首日", "站上MA200日"],
               [[c["symbol"], c.get("cg_rank") or "—", money(c["turnover"]["median"]), money(c["turnover"]["min"]), money(c["turnover"]["last30_avg"]),
                 money(c.get("cg_vol24")), pc(c["ret_end"], 0), pc(c["max_rally"], 0), dt(c["up20"]), dt(c["ma200"].get("cross_date"))]
                for c in sorted(ranked, key=lambda c: c["turnover"]["median"] if c["turnover"]["median"] is not None else 0) if c["low_activity"] or c.get("low_now")])
    notes = [
        "币种名单：" + meta["universe_note"],
        "“启动日”：相对到当天为止的区间最低收盘，第一次涨超 +20% / +50% / +100% 的日期；“高点”和“低点”均用收盘价。",
        "站上均线：收盘价从不高于 SMA 变成高于 SMA 的第一天；起点前一天已在均线上的，在“站上日”列显示“—”，排序和综合分里按“起点前就在上方”算最早。MA365 需要至少 365 根日线，不足显示“不足”。",
        "MACD 参数 12/26/9；周线按 ISO 周（周一到周日，UTC）取每周最后一根收盘，最后一周可能没走完；“金叉”= DIF 上穿 DEA，“零轴”= DIF 上穿 0。",
        "综合分 = 0.3×启动早晚分 + 0.3×区间收益分 + 0.2×均线分 + 0.2×周线MACD分（DIF在零轴上且柱子为红 1.0；仅DIF在零轴上 0.6；仅柱子为红 0.4；都不是 0）；各分项都是在本批币里的相对位置（最好 = 1）。权重是我定的，改权重会改排名。",
        "成交活跃度：币安现货接口在这台服务器上取不到，所以用币安 U 本位永续合约的日成交额（USDT）当代理，窗口内取中位数；永续成交额通常高于同币现货，所以按同一个门槛会少标出一部分现货很冷清的币。另列 CoinGecko 的全市场 24h 成交额（现货为主、多交易所合计，只有“现在”这一个数）作对照。",
        "局限：名单按“现在”的市值榜取，区间里涨起来又掉出前列、或后来才上市的币不在内（幸存者偏差）；用的是永续合约价格，不是现货；没有计入资金费率。",
    ] + meta["warnings"]
    css = ("body{font:14px/1.6 -apple-system,'PingFang SC',sans-serif;margin:0;padding:16px;background:#fff;color:#1a1a1a;max-width:1100px;margin:auto}"
           "h1{font-size:22px}h2{font-size:17px;margin-top:28px}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px}"
           "th,td{border-bottom:1px solid #ddd;padding:5px 8px;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left;font-weight:600}"
           "th{background:#f4f4f4;position:sticky;top:0}svg{width:100%;height:auto;font-size:11px}svg text{fill:#555}.grid{stroke:#e3e3e3}.mark{stroke:#999;stroke-dasharray:3 3}"
           "h3{font-size:14px;margin:18px 0 6px}li{margin:4px 0}.note{color:#666;font-size:12.5px}"
           "@media(prefers-color-scheme:dark){body{background:#15171a;color:#e8e8e8}th{background:#22262b}td,th{border-color:#333}svg text{fill:#aaa}.grid{stroke:#2c3036}.note{color:#999}}")
    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>龙头与轮动研究 {start}~{end}</title><style>{css}</style></head><body>"
            f"<h1>龙头与轮动研究：{start} → {end}</h1><p class='note'>生成于 {meta['generated']}（北京时间） · 数据到 {meta['last_bar']}</p>"
            "<h2>摘要</h2><ul>" + "".join(f"<li>{html.escape(s)}</li>" for s in summary) + "</ul>" +
            queue_html + "<h2>1. 先涨后涨：全部币的明细</h2>" + t1 +
            "<h2>2. 区间走势（周收盘，基准日 = 100；虚线 = BTC，竖线 = 区间起止）</h2>" + chart([c for c in top if c is not btc], btc, start, end) +
            "<p class='note'>图里画的是综合分前 5 名和 BTC。</p>"
            "<h2>3. 200 日 / 365 日均线：谁先站上</h2>" + t2 +
            "<h2>4. MACD（周线为主，日线对照）</h2>" + t3 +
            "<h2>5. 价格结构与龙头综合排序</h2>" + t4 +
            f"<h2>6. 低活跃币（窗口内日成交额中位数低于 {money(meta['min_turnover'])} 美元，或现在全市场24h成交额低于它）</h2>" + (t5 if has_low else "<p class='note'>没有符合条件的币。</p>") +
            "<h2>7. 口径与局限</h2><ul class='note'>" + "".join(f"<li>{html.escape(s)}</li>" for s in notes) + "</ul></body></html>")


# ---------- 主流程 ----------
def run(start, end, top_n, out_dir, getter=monitor.http_json, now=None, min_turnover=5e6):
    now = now or datetime.now(timezone.utc)
    warnings = []
    bases = perp_bases(getter)
    candidates = universe(top_n, getter, bases, warnings)
    coins, skipped = [], []
    for cand in candidates:
        if len(coins) >= top_n:
            break
        try:
            bars = [b for b in fetch_daily(cand["contract"], getter) if b["close_time"] < int(now.timestamp() * 1000)]
        except Exception as exc:  # noqa: BLE001
            skipped.append({"symbol": cand["symbol"], "skipped": str(exc)[:100]})
            continue
        scale = 1000 if cand["contract"].startswith("1000") else 1
        if cand.get("cg_price") and not 0.8 < bars[-1]["close"] / scale / cand["cg_price"] < 1.25:
            skipped.append({"symbol": cand["symbol"], "skipped": "币安合约价格与市值榜价格对不上（疑似同名不同币）"})
            continue
        if len(bars) < 230:
            skipped.append({"symbol": cand["symbol"], "skipped": f"日线只有 {len(bars)} 根，不够算 MA200"})
            continue
        result = analyze(cand["symbol"], bars, start, end, min_turnover)
        if "skipped" in result:
            skipped.append(result)
            continue
        result.update(name=cand["name"], cg_rank=cand["cg_rank"], mcap=cand["mcap"], contract=cand["contract"], cg_vol24=cand.get("cg_vol24"))
        result["low_now"] = cand.get("cg_vol24") is not None and cand["cg_vol24"] < min_turnover
        coins.append(result)
        time.sleep(0.15)
    if not coins:
        raise RuntimeError("没有任何币分析成功：" + "；".join(f"{s['symbol']}:{s['skipped']}" for s in skipped[:5]))
    ranked = rank_all(coins)
    btc = next((c for c in ranked if c["symbol"] == "BTC"), None)
    if not btc:
        warnings.append("名单里没有 BTC，相对 BTC 一列为空")
    kind = "CoinGecko 市值榜（取数时的当前排名）" if all(c.get("cg_rank") for c in ranked) else "币安 24h 成交额排序"
    meta = {"start": start.isoformat(), "end": end.isoformat(), "top_n": top_n, "min_turnover": min_turnover, "generated": (now + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M"),
            "last_bar": max(c["last_date"] for c in ranked), "warnings": warnings,
            "universe_note": f"{kind}前 {len(ranked)} 个、币安有 USDT 永续合约，已剔除稳定币/包装币/黄金币；" + "；".join(
                f"{s['symbol']} 未纳入（{s['skipped']}）" for s in skipped) if skipped else f"{kind}前 {len(ranked)} 个、币安有 USDT 永续合约，已剔除稳定币/包装币/黄金币"}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rotation_report.html").write_text(build_html(meta, ranked, skipped, btc), encoding="utf-8")
    (out / "rotation_data.json").write_text(json.dumps({"meta": meta, "coins": ranked, "skipped": skipped}, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta, ranked


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--top", type=int, default=200)
    parser.add_argument("--min-turnover", type=float, default=5e6, help="日成交额低于这个数（美元）算低活跃")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-10-31")
    args = parser.parse_args(argv)
    meta, ranked = run(date.fromisoformat(args.start), date.fromisoformat(args.end), args.top, args.out, min_turnover=args.min_turnover)
    print(f"完成：{len(ranked)} 个币，综合分前 5：" + "、".join(c["symbol"] for c in ranked[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
