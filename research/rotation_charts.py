#!/usr/bin/env python3
"""龙头 / 轮动研究的图表版：把 rotation_data.json 变成一页统计图（纯 SVG，无外部依赖，手机能看）。

明细表仍在 rotation_report.html；这一页只回答几个问题，每张图上面有一句结论：
  谁先谁后 → 启动时间分布；BTC 拉升前后 → 先行/跟涨/掉队的散点；大币小币 → 市值分档对比；
  现在结构 → 均线与 MACD 状态分布；谁最强 → 收益榜与综合分热力图；冷门币 → 成交额 vs 涨幅。
用法：python3 research/rotation_charts.py rotation_data.json 输出.html
"""
import html
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

CLASSES = [("龙头", "c1", "先行且续强"), ("先行后掉队", "c2", "锚点前领先，锚点后30天不如BTC"),
           ("后发跟涨", "c3", "锚点前没领先，锚点后30天不低于BTC"), ("落后", "c4", "两头都不如BTC"), ("无法分类", "c5", "")]
STRUCTS = [("强势多头", "c1"), ("修复中", "c3"), ("筑底", "c2"), ("弱势", "c4"), ("数据不足", "c5")]
W = 520


def e(text):
    return html.escape(str(text))


def pct(v, digits=0):
    return "—" if v is None else f"{v * 100:+.{digits}f}%"


def md(s):
    return "—" if not s else s[5:]


def money(v):
    return "—" if v is None else (f"{v / 1e8:.1f}亿" if v >= 1e8 else f"{v / 1e4:,.0f}万")


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def cls_key(c):
    return (c.get("anchor_class") or "无法分类").split("（")[0]


def cls_css(name):
    return next((css for label, css, _ in CLASSES if label == name), "c5")


def week_start(d):
    return d - timedelta(days=d.weekday())


# ---------- 图元 ----------
def hbars(rows, width=W, label_w=64, bar_h=18, gap=5, fmt=lambda v: f"{v:g}", note_w=90):
    """rows: [(标签, 数值, css, 右侧小字)]"""
    if not rows:
        return "<p class='note'>没有数据。</p>"
    top = max(abs(v) for _, v, _, _ in rows) or 1
    height = len(rows) * (bar_h + gap) + 4
    inner = width - label_w - note_w - 56
    out = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for i, (label, value, css, note) in enumerate(rows):
        y = 2 + i * (bar_h + gap)
        w = max(1.5, abs(value) / top * inner)
        out.append(f'<text x="{label_w - 6}" y="{y + bar_h - 5}" text-anchor="end" class="lab">{e(label)}</text>'
                   f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="2" class="{css}"><title>{e(label)} {e(fmt(value))}</title></rect>'
                   f'<text x="{label_w + w + 5:.1f}" y="{y + bar_h - 5}" class="val">{e(fmt(value))}</text>')
        if note:
            out.append(f'<text x="{width - 4}" y="{y + bar_h - 5}" text-anchor="end" class="mut">{e(note)}</text>')
    out.append("</svg>")
    return "".join(out)


def histogram(dates, start, end, marks, height=190, left=34):
    """按周统计的柱状图 + 累计占比折线。dates: date 列表；marks: [(date, 文字)]"""
    if not dates:
        return "<p class='note'>没有数据。</p>"
    first, last = week_start(min(min(dates), start)), week_start(max(max(dates), end))
    weeks = []
    cur = first
    while cur <= last:
        weeks.append(cur)
        cur += timedelta(days=7)
    counts = [sum(1 for d in dates if week_start(d) == w) for w in weeks]
    peak = max(counts) or 1
    inner_w = W - left - 12
    bw = inner_w / len(weeks)
    base_y, plot_h = height - 24, height - 24 - 18
    out = [f'<svg viewBox="0 0 {W} {height}" role="img">']
    for t in range(0, 5):
        v = peak * t / 4
        y = base_y - plot_h * t / 4
        out.append(f'<line x1="{left}" x2="{W - 8}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/><text x="{left - 5}" y="{y + 4:.1f}" text-anchor="end" class="mut">{v:.0f}</text>')
    total, run, pts = len(dates), 0, []
    for i, (w, n) in enumerate(zip(weeks, counts)):
        x = left + i * bw
        h = plot_h * n / peak
        out.append(f'<rect x="{x + 1:.1f}" y="{base_y - h:.1f}" width="{max(bw - 2, 1):.1f}" height="{h:.1f}" rx="1.5" class="c3"><title>{w}起一周：{n} 个</title></rect>')
        run += n
        pts.append(f"{x + bw / 2:.1f},{base_y - plot_h * run / total:.1f}")
    out.append(f'<polyline points="{" ".join(pts)}" fill="none" class="line"/>')
    step = max(1, len(weeks) // 8)
    for i in range(0, len(weeks), step):
        out.append(f'<text x="{left + i * bw + bw / 2:.1f}" y="{height - 6}" text-anchor="middle" class="mut">{weeks[i].strftime("%m-%d")}</text>')
    for k, (d, text) in enumerate(marks):
        if not d:
            continue
        x = left + ((week_start(d) - first).days / 7) * bw + bw / 2
        if left <= x <= W - 8:
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="14" y2="{base_y}" class="mark"/><text x="{min(x + 4, W - 110):.1f}" y="{12 + k * 11}" class="lab">{e(text)}</text>')
    out.append("</svg>")
    return "".join(out)


def scatter(points, xlab, ylab, xlines=(), ylines=(), height=330, left=44, bottom=34, labels=(), xfmt=lambda v: f"{v:.0f}", yfmt=lambda v: f"{v:.0f}"):
    """points: [(x, y, css, 悬停文字)]。极端值收在边缘（取 3%-97% 分位再留边），不让一两个离群点把图压扁。"""
    if not points:
        return "<p class='note'>没有数据。</p>"
    xs, ys = sorted(p[0] for p in points), sorted(p[1] for p in points)
    pick = lambda arr, q: arr[min(len(arr) - 1, max(0, int(q * (len(arr) - 1))))]  # noqa: E731
    x0, x1, y0, y1 = pick(xs, 0.03), pick(xs, 0.97), pick(ys, 0.03), pick(ys, 0.97)
    for v in xlines:
        x0, x1 = min(x0, v), max(x1, v)
    for v in ylines:
        y0, y1 = min(y0, v), max(y1, v)
    px, py = (x1 - x0) * 0.06 or 1, (y1 - y0) * 0.06 or 1
    x0, x1, y0, y1 = x0 - px, x1 + px, y0 - py, y1 + py
    X = lambda v: left + (min(max(v, x0), x1) - x0) / (x1 - x0) * (W - left - 14)  # noqa: E731
    Y = lambda v: 10 + (y1 - min(max(v, y0), y1)) / (y1 - y0) * (height - bottom - 10)  # noqa: E731
    out = [f'<svg viewBox="0 0 {W} {height}" role="img">']
    for t in range(5):
        gy, gx = y0 + (y1 - y0) * t / 4, x0 + (x1 - x0) * t / 4
        out.append(f'<line x1="{left}" x2="{W - 14}" y1="{Y(gy):.1f}" y2="{Y(gy):.1f}" class="grid"/><text x="{left - 5}" y="{Y(gy) + 4:.1f}" text-anchor="end" class="mut">{e(yfmt(gy))}</text>'
                   f'<text x="{X(gx):.1f}" y="{height - bottom + 14}" text-anchor="middle" class="mut">{e(xfmt(gx))}</text>')
    for v in xlines:
        out.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="10" y2="{height - bottom}" class="mark"/>')
    for v in ylines:
        out.append(f'<line x1="{left}" x2="{W - 14}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="mark"/>')
    for x, y, css, tip in points:
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4.2" class="{css} dot"><title>{e(tip)}</title></circle>')
    for x, y, text in labels:
        out.append(f'<text x="{X(x) + 6:.1f}" y="{Y(y) - 5:.1f}" class="lab">{e(text)}</text>')
    out.append(f'<text x="{(left + W - 14) / 2:.1f}" y="{height - 4}" text-anchor="middle" class="mut">{e(xlab)}</text>'
               f'<text x="12" y="{(height - bottom) / 2:.1f}" text-anchor="middle" class="mut" transform="rotate(-90 12 {(height - bottom) / 2:.1f})">{e(ylab)}</text></svg>')
    return "".join(out)


def heatmap(rows, cols):
    """rows: [(标签, [0-1 的分数...], [显示文字...])]"""
    cw, rh, label_w, head = 118, 20, 78, 22
    height = head + len(rows) * rh + 4
    width = label_w + cw * len(cols)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for j, name in enumerate(cols):
        out.append(f'<text x="{label_w + j * cw + cw / 2}" y="14" text-anchor="middle" class="mut">{e(name)}</text>')
    for i, (label, scores, texts) in enumerate(rows):
        y = head + i * rh
        out.append(f'<text x="{label_w - 6}" y="{y + 14}" text-anchor="end" class="lab">{e(label)}</text>')
        for j, (s, t) in enumerate(zip(scores, texts)):
            opacity = 0.12 + 0.88 * max(0.0, min(1.0, s))
            out.append(f'<rect x="{label_w + j * cw + 1}" y="{y + 1}" width="{cw - 2}" height="{rh - 2}" rx="2" class="c1" style="opacity:{opacity:.2f}"/>'
                       f'<text x="{label_w + j * cw + cw / 2}" y="{y + 14}" text-anchor="middle" class="cell">{e(t)}</text>')
    out.append("</svg>")
    return f'<div class="scroll">{"".join(out)}</div>'


def legend(items):
    return '<div class="legend">' + "".join(f'<span><i class="sw {css}"></i>{e(name)}</span>' for name, css in items) + "</div>"


def kpi(label, value, sub=""):
    return f'<div class="kpi"><div class="kv">{e(value)}</div><div class="kl">{e(label)}</div>' + (f'<div class="ks">{e(sub)}</div>' if sub else "") + "</div>"


def section(title, takeaway, body, note=""):
    return (f"<section><h2>{e(title)}</h2><p class='take'>{e(takeaway)}</p>{body}" + (f"<p class='note'>{e(note)}</p>" if note else "") + "</section>")


# ---------- 页面 ----------
def build(data):
    meta, coins = data["meta"], data["coins"]
    start, end = date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"])
    anchor = date.fromisoformat(meta["anchor"]) if meta.get("anchor") else None
    btc = next((c for c in coins if c["symbol"] == "BTC"), None)
    n = len(coins)
    started = [c for c in coins if c.get("w50") and c["w50"] <= meta["end"]]
    btc_w50 = date.fromisoformat(btc["w50"]) if btc and btc.get("w50") else None
    above_base = sum(1 for c in coins if c["ma200"].get("above_at_base"))
    above_now = sum(1 for c in coins if c["ma200"].get("above_now"))
    classes = {label: [c for c in coins if cls_key(c) == label] for label, _, _ in CLASSES}
    leaders = classes["龙头"]
    parts = []

    # KPI
    kpis = [kpi("分析币数", str(n), f"{meta['start']} → {meta['end']}"),
            kpi("BTC 区间收益", pct(btc["ret_end"]) if btc else "—", f"现在 {pct(btc['ret_now'])}" if btc else ""),
            kpi("站上 MA200", f"{above_now}/{n}", f"起点时 {above_base}/{n}"),
            kpi("区间内涨到低点+50%", f"{len(started)}/{n}", f"{len(started) * 100 // max(n, 1)}% 的币"),
            kpi("龙头（先行且续强）", str(len(leaders)) if anchor else "—", f"锚点 {meta['anchor']}" if anchor else "")]
    parts.append('<div class="kpis">' + "".join(kpis) + "</div>")

    # 1 谁先谁后
    dates = [date.fromisoformat(c["w50"]) for c in started]
    if dates and btc_w50:
        before = sum(1 for d in dates if d < btc_w50)
        after = sum(1 for d in dates if d > btc_w50)
        same = len(dates) - before - after
        take = (f"BTC 在 {md(btc['w50'])} 涨到区间低点 +50%；比 BTC 更早启动的币 {before} 个、同一天 {same} 个、更晚 {after} 个。"
                f"启动日期中位数 {md(date.fromordinal(int(statistics.median([d.toordinal() for d in dates]))).isoformat())}。")
    else:
        take = f"区间内 {len(started)} 个币涨到过低点 +50%。"
    marks = [(btc_w50, "BTC 启动")] + ([(anchor, "锚点")] if anchor else [])
    parts.append(section("1. 谁先启动、谁后启动", take, histogram(dates, start, end, marks) + legend([("每周启动的币数", "c3")]) + "<p class='note'>柱 = 当周首次涨到“区间低点之后 +50%”的币数；折线 = 累计占已启动币的比例（纵轴 0–100%）。</p>"))

    # 2 锚点分类
    if anchor and btc and btc.get("anchor"):
        b = btc["anchor"]
        rows = [(label, len(classes[label]), css, "") for label, css, desc in CLASSES if classes[label]]
        pts = []
        for c in coins:
            a = c.get("anchor")
            if not a or a.get("r30") is None:
                continue
            rel = (a["pre_ret"] - b["pre_ret"]) * 100
            tip = f"{c['symbol']}｜{cls_key(c)}｜锚点前相对BTC {rel:+.0f}pp｜锚点后30天 {a['r30'] * 100:+.0f}%"
            pts.append((rel, a["r30"] * 100, cls_css(cls_key(c)), tip))
        top_labels = sorted((c for c in leaders if c.get("anchor") and c["anchor"].get("r30") is not None), key=lambda c: -c["anchor"]["r30"])[:5]
        lab = [((c["anchor"]["pre_ret"] - b["pre_ret"]) * 100, c["anchor"]["r30"] * 100, c["symbol"]) for c in top_labels]
        take = (f"以 {meta['anchor']} 为界：龙头 {len(classes['龙头'])} 个、先行后掉队 {len(classes['先行后掉队'])} 个、后发跟涨 {len(classes['后发跟涨'])} 个、落后 {len(classes['落后'])} 个。"
                f"BTC 自己锚点前 {pct(b['pre_ret'])}、锚点后30天 {pct(b['r30'])}。")
        body = (hbars(rows, label_w=84, note_w=0, fmt=lambda v: f"{v:.0f} 个") + "<p class='note'>龙头 = 锚点前比 BTC 多涨 10 个百分点以上，且锚点后 30 天收益不低于 BTC；先行后掉队 = 只满足前一条；后发跟涨 = 只满足后一条；落后 = 都不满足。</p>" +
                "<h3>每个点 = 一个币（右上 = 先行且续强；鼠标/长按看币名）</h3>" +
                scatter(pts, "锚点前收益 − BTC 锚点前收益（百分点）", "锚点后30天收益 %", xlines=(10,), ylines=(b["r30"] * 100,), labels=lab) +
                legend([(label, css) for label, css, _ in CLASSES if classes[label]]) +
                "<p class='note'>竖线 = 先行门槛（比 BTC 多涨 10 个百分点）；横线 = BTC 锚点后30天收益（续强门槛）。超出图框的极端值贴在边缘。</p>")
        parts.append(section("2. BTC 拉升前后：先行、跟涨、掉队", take, body))

    # 3 市值分档
    tiers = [("前30名", 1, 30), ("31–100名", 31, 100), ("101名以后", 101, 10 ** 6)]
    trows = []
    for name, lo, hi in tiers:
        g = [c for c in coins if c.get("cg_rank") and lo <= c["cg_rank"] <= hi]
        if g:
            gs = [c for c in g if c.get("w50") and c["w50"] <= meta["end"]]
            trows.append((name, len(g), len(gs) / len(g), median([c["ret_end"] for c in g]), median([date.fromisoformat(c["w50"]).toordinal() for c in gs]),
                          median([c["max_rally"] for c in g])))
    if trows:
        small = lambda title, items, fmt: f"<div class='mini'><h3>{e(title)}</h3>{hbars(items, width=300, label_w=64, note_w=0, fmt=fmt)}</div>"  # noqa: E731
        first_day = min((r[4] for r in trows if r[4]), default=None)
        body = ('<div class="grid3">'
                + small("涨到低点+50%的占比", [(f"{r[0]}", r[2] * 100, "c3", "") for r in trows], lambda v: f"{v:.0f}%")
                + small("区间收益中位数", [(f"{r[0]}", r[3] * 100, "c1", "") for r in trows], lambda v: f"{v:+.0f}%")
                + small("启动日晚于最早一档（天）", [(f"{r[0]}", (r[4] - first_day) if r[4] else 0, "c2", "") for r in trows], lambda v: f"{v:+.0f}天")
                + "</div>")
        take = "；".join(f"{r[0]}（{r[1]}个）：{r[2] * 100:.0f}% 启动、收益中位 {r[3] * 100:+.0f}%、最大涨幅中位 {r[5] * 100:+.0f}%" for r in trows) + "。"
        parts.append(section("3. 大币和小币谁先动", take, body))

    # 4 结构分布
    sc = {}
    for c in coins:
        sc.setdefault(c["structure"].split("（")[0], []).append(c)
    srows = [(label, len(sc.get(label, [])), css, "") for label, css in STRUCTS if sc.get(label)]
    cross_dates = [date.fromisoformat(c["ma200"]["cross_date"]) for c in coins if c["ma200"].get("cross_date")]
    pre_above = sum(1 for c in coins if c["ma200"].get("above_at_base"))
    take = (f"现在价格结构：" + "、".join(f"{label} {len(sc[label])}" for label, _ in STRUCTS if sc.get(label)) + f"。区间开始时已在 MA200 上方 {pre_above} 个，区间内新站上 {len(cross_dates)} 个。")
    body = (hbars(srows, label_w=70, note_w=0, fmt=lambda v: f"{v:.0f} 个") + "<h3>区间内新站上 MA200 的时间分布</h3>" +
            histogram(cross_dates, start, end, marks=[(btc_w50, "BTC 启动")] if btc_w50 else []) +
            "<p class='note'>结构口径：强势多头 = 站上 MA200（且 MA365 不在下方）且周线 MACD 在零轴上；修复中 = 站上 MA200 但另一项不满足；筑底 = MA200 下方但周线动能回升；弱势 = MA200 下方且动能向下。</p>")
    parts.append(section("4. 均线与结构：谁已经站稳", take, body))

    # 5 收益榜 + 热力图
    top_ret = sorted(coins, key=lambda c: -c["ret_end"])[:25]
    rows = [(c["symbol"], c["ret_end"] * 100, cls_css(cls_key(c)) if anchor else "c3",
             f"启动 {md(c.get('w50'))}" + (" · 低活跃" if c["low_activity"] else "")) for c in top_ret]
    take = f"区间收益前 25 名里，{sum(1 for c in top_ret if c.get('w50') and btc_w50 and date.fromisoformat(c['w50']) <= btc_w50)} 个比 BTC 更早（或同日）启动；其中低活跃币 {sum(1 for c in top_ret if c['low_activity'])} 个。"
    parts.append(section("5. 区间收益前 25 名", take, hbars(rows, label_w=64, note_w=118, fmt=lambda v: f"{v:+.0f}%") + (legend([(label, css) for label, css, _ in CLASSES if classes[label]]) if anchor else "")))
    top_comp = sorted(coins, key=lambda c: -c["composite"])[:30]
    hm = [(f"{c['symbol']} {c['composite']:.2f}", [c["scores"]["start"], c["scores"]["ret"], c["scores"]["ma"], c["scores"]["macd"]],
           [md(c.get("w50")) if c.get("w50") else "未启动", pct(c["ret_end"]), "站上" if c["ma200"].get("above_now") else "MA200下", "零轴上" if c["macd_w"]["dif_above_zero_now"] else "零轴下"])
          for c in top_comp]
    parts.append(section("6. 综合分前 30 名：强在哪一项", "颜色越深，该项在本批币里的位置越靠前；综合分 = 0.3×启动早晚 + 0.3×区间收益 + 0.2×均线 + 0.2×周线MACD，只是排序，不是买卖建议。",
                         heatmap(hm, ["启动日(+50%)", "区间收益", "现价 vs MA200", "周线MACD"])))

    # 7 成交额 vs 涨幅
    pts = []
    import math
    for c in coins:
        med = c["turnover"].get("median")
        if med and med > 0:
            pts.append((math.log10(med), c["ret_end"] * 100, "c4" if c["low_activity"] else "c3",
                        f"{c['symbol']}｜日成交额中位 {money(med)}｜区间收益 {c['ret_end'] * 100:+.0f}%"))
    low = [c for c in coins if c["low_activity"]]
    act = [c for c in coins if not c["low_activity"]]
    take = (f"低活跃币（窗口内日成交额中位数低于 {money(meta['min_turnover'])} 美元）{len(low)} 个，收益中位 {pct(median([c['ret_end'] for c in low]))}；"
            f"活跃币 {len(act)} 个，收益中位 {pct(median([c['ret_end'] for c in act]))}。") if low else f"没有低于 {money(meta['min_turnover'])} 美元的低活跃币。"
    parts.append(section("7. 成交额和涨幅：冷门币涨得怎么样", take,
                         scatter(pts, "日成交额中位数（永续，美元，对数轴）", "区间收益 %", xlines=(math.log10(meta["min_turnover"]),),
                                 xfmt=lambda v: money(10 ** v)) + legend([("活跃", "c3"), ("低活跃", "c4")]) +
                         "<p class='note'>竖线 = 低活跃门槛。成交额用币安永续合约代替现货，通常偏高。</p>"))

    # 8 龙头清单
    if anchor and leaders:
        lead_rows = sorted(leaders, key=lambda c: -c["anchor"]["r30"])[:15]
        cells = "".join(f"<tr><td>{e(c['symbol'])}</td><td>{e(c.get('cg_rank') or '—')}</td><td>{pct(c['anchor']['pre_ret'])}</td><td>{pct(c['anchor']['r30'])}</td>"
                        f"<td>{'—' if c['anchor']['react20'] is None else str(c['anchor']['react20']) + '天'}</td><td>{md(c['anchor']['peak_date'])}</td><td>{pct(c['anchor']['dd_now'])}</td></tr>" for c in lead_rows)
        parts.append(section("8. 龙头清单（锚点后30天收益前15）", "先行且续强的币；完整的 200 个币明细在 rotation_report.html。",
                             f"<div class='scroll'><table><thead><tr><th>币</th><th>市值榜</th><th>锚点前收益</th><th>锚点后30天</th><th>涨20%用时</th><th>高点日</th><th>高点至今</th></tr></thead><tbody>{cells}</tbody></table></div>"))

    css = (":root{--bg:#fff;--fg:#1a1a1a;--mut:#6b7280;--grid:#e5e7eb;--card:#f6f7f9;--c1:#0f9d8a;--c2:#e08a00;--c3:#3b6fd4;--c4:#9aa1ab;--c5:#c9cdd3;--line:#1a1a1a}"
           "@media(prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e8e8e8;--mut:#9aa1ab;--grid:#2a2e35;--card:#1d2026;--c1:#35c2ad;--c2:#f0a52e;--c3:#6f9bf0;--c4:#6f7783;--c5:#454b55;--line:#e8e8e8}}"
           "*{box-sizing:border-box}body{font:14px/1.6 -apple-system,'PingFang SC',sans-serif;margin:0 auto;padding:16px;background:var(--bg);color:var(--fg);max-width:820px}"
           "h1{font-size:21px;margin:4px 0}h2{font-size:17px;margin:0 0 4px}h3{font-size:13px;margin:16px 0 4px;color:var(--mut);font-weight:600}section{margin:30px 0;padding-top:6px;border-top:1px solid var(--grid)}"
           ".take{margin:2px 0 10px;font-weight:600}.note{color:var(--mut);font-size:12.5px}svg{width:100%;height:auto;display:block}svg text{fill:var(--fg);font-size:11.5px}"
           "svg .mut{fill:var(--mut);font-size:10.5px}svg .lab{font-size:11px}svg .val{font-size:11px;font-weight:600}svg .cell{font-size:10.5px;fill:var(--fg)}"
           ".grid{stroke:var(--grid)}.mark{stroke:var(--mut);stroke-dasharray:3 3}.line{stroke:var(--line);stroke-width:1.6}"
           ".c1{fill:var(--c1);background:var(--c1)}.c2{fill:var(--c2);background:var(--c2)}.c3{fill:var(--c3);background:var(--c3)}.c4{fill:var(--c4);background:var(--c4)}.c5{fill:var(--c5);background:var(--c5)}"
           ".dot{opacity:.78;stroke:var(--bg);stroke-width:.6}.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:8px 0;font-size:12.5px}.legend .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}"
           ".kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));gap:10px;margin:14px 0}.kpi{background:var(--card);border-radius:8px;padding:10px 12px}"
           ".kv{font-size:22px;font-weight:700}.kl{color:var(--mut);font-size:12px}.ks{font-size:11.5px;color:var(--mut)}.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}"
           ".scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{border-bottom:1px solid var(--grid);padding:5px 8px;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left;font-weight:600}th{background:var(--card)}")
    return ("<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>龙头与轮动·图表版</title><style>{css}</style></head><body>"
            f"<h1>龙头与轮动研究·图表版</h1><p class='note'>{e(meta['start'])} → {e(meta['end'])} · 数据到 {e(meta['last_bar'])} · 生成于 {e(meta['generated'])}（北京时间）· 名单：{e(meta['universe_note'][:90])}。完整明细表见 rotation_report.html。</p>"
            + "".join(parts) + "</body></html>")


def main(argv):
    if len(argv) != 2:
        print("用法：rotation_charts.py rotation_data.json 输出.html")
        return 2
    data = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    Path(argv[1]).write_text(build(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
