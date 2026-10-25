# -*- coding: utf-8 -*-
"""周日「下周清单」的数据包与文案（纯函数，不联网）。

build_pack()  把日历 JSON（uncleonchain.com/data/cal/cal_latest.json）、Notion 台账、日线收盘价整理成一个 pack；
view()        从 pack 取出海报上每一块的中英文字符串；
compose_posts() 从同一个 pack 生成中文帖和英文帖。
海报和帖子读同一个 pack，数字不会各写各的。拿不到的数据就是缺项，不估算、不补。
"""
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_USD = 2000.0          # 每笔名义仓位基数，仓位列是它的倍数
START_EQUITY_FALLBACK = 10000.0
WD_ZH = "一二三四五六日"
WD_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MON_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HEADER_ZH, HEADER_EN = "【$BTC下周清单】", "【$BTC Week Ahead】"


def load_rules():
    return json.loads((HERE / "major_events.json").read_text(encoding="utf-8"))


def d(s):
    return date.fromisoformat(s[:10])


def md(s):
    return f"{int(s[5:7]):02d}-{int(s[8:10]):02d}"


def md_en(s):
    return f"{MON_EN[int(s[5:7]) - 1]} {int(s[8:10])}"


def span_zh(a, b):
    if a == b:
        return md(a)
    return f"{md(a)}→{md(b)[3:] if a[5:7] == b[5:7] else md(b)}"


def span_en(a, b):
    if a == b:
        return md_en(a)
    return f"{md_en(a)}→{b[8:10].lstrip('0') if a[5:7] == b[5:7] else md_en(b)}"


def limit_cashtags(text):
    """与 cloud/formal.mjs 的 limitCashtags 一致：X 只允许一个 $代币，其余去掉 $。"""
    seen = [False]

    def sub(m):
        if not seen[0]:
            seen[0] = True
            return m.group(0)
        return m.group(1)
    return re.sub(r"\$([A-Za-z][A-Za-z0-9]{1,9})(?![A-Za-z0-9_])", sub, text)   # 与 JS 的 \b 一样只认 ASCII 边界


def week_window(today):
    t = d(today)
    start = t + timedelta(days=7 - t.weekday())
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


# ───────────────────────── 日历 → 事件 ─────────────────────────
def _events(cal, start, end):
    out, untested = [], []
    for e in (cal.get("btc") or {}).get("events", []):
        bj = (e.get("time_bj") or e.get("date") or "")
        day = bj[:10]
        if not (start <= day <= end):
            continue
        imp = e.get("impact") or {}
        if not e.get("tested") or not imp:
            untested.append({"title_zh": e.get("title_zh", ""), "title_en": e.get("title_en", ""), "time_bj": bj})
            continue
        utc = e.get("time_utc") or ""
        close_bj = ""
        if utc:   # 事件当天 UTC 日线 00:00 UTC 收盘 = 北京时间 08:00
            u = datetime.strptime(utc, "%Y-%m-%d %H:%M")
            close = (u + timedelta(days=1)).replace(hour=0, minute=0) + timedelta(hours=8)
            close_bj = close.strftime("%Y-%m-%d %H:%M")
        out.append({"type": e.get("type"), "title_zh": e["title_zh"], "title_en": e["title_en"], "time_bj": bj,
                    "time_et": e.get("time_et", ""), "text_zh": imp.get("text_zh", ""), "text_en": imp.get("text_en", ""),
                    "use_zh": e.get("use_zh", ""), "use_en": e.get("use_en", ""),
                    "range_typ": imp.get("range_2h_typ_pct"), "range_normal": imp.get("range_2h_normal_pct"),
                    "close_bj": close_bj})
    out.sort(key=lambda x: x["time_bj"])
    return out, untested


def _conf_title(e):
    return (e.get("title") or "").lower()


def _pick(events, rules, start, end, excl, kinds, need=None):
    rows = []
    for rule in rules:
        best = None
        for e in events:
            if e.get("category") not in kinds or not (start <= e["date_start"] <= end):
                continue
            t = _conf_title(e)
            if any(x in t for x in excl) or not any(m in t for m in rule["match"]):
                continue
            if need and not any(w in t for w in need):
                continue
            days = (d(e["date_end"]) - d(e["date_start"])).days + 1
            if days < rule.get("min_days", 1) or days > 40:
                continue
            if best is None or days > best[0]:
                best = (days, e)
        if best:
            e = best[1]
            rows.append({"start": e["date_start"], "end": e["date_end"], "zh": rule["zh"], "en": rule["en"],
                         "city_en": e.get("city", ""), "online": "online" in _conf_title(e)})
    rows.sort(key=lambda r: (r["start"], r["zh"]))
    return rows


def _web3(cal, start, end, rules):
    evs = (cal.get("web3") or {}).get("events", [])
    cities = rules.get("cities", {})
    conf = _pick(evs, rules["conferences"], start, end, rules["exclude"], ("会议",))
    hack = _pick(evs, rules["hackathons"], start, end, rules["exclude"], ("黑客松", "会议"), need=("hackathon", "buildathon", "hacker house"))
    for r in conf + hack:
        c = (r["city_en"] or "").split(",")[0].strip()
        r["city_zh"] = cities.get(c, c)
        r["city_en"] = c
    return conf, hack


def _unlocks(cal, start, end):
    u = cal.get("unlocks")
    if not isinstance(u, dict):
        return {"available": False, "items": []}
    items = []
    for e in u.get("events", []):
        if not e.get("important") or not (start <= e["date"] <= end):
            continue
        kz = re.sub(r"（[^）]*）", "", e.get("kind") or "").strip()
        ke = (e.get("kind_en") or "").strip()
        items.append({"date": e["date"], "sym": e["symbol"], "kind_zh": kz, "kind_en": ke,
                      "usd_m": e.get("usd_m"), "pct_circ": e.get("pct_circ"), "amount": e.get("amount") or ""})
    items.sort(key=lambda x: (x["date"], x["sym"]))
    return {"available": True, "items": items}


# ───────────────────────── 台账 → 持仓与周报 ─────────────────────────
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _notional(rec, note):
    m = re.search(r"名义约\s*([\d,]+(?:\.\d+)?)\s*美元", note or "")
    if m:
        return float(m.group(1).replace(",", ""))
    s = _num(rec.get("size"))
    return round(BASE_USD * s, 2) if s is not None else None


def _position(rec, note, prices):
    sym, side = rec["symbol"], rec.get("side") or ""
    short = side == "做空"
    px = (prices.get(sym) or {}).get("close")
    entry, stop, target = _num(rec.get("entry")), _num(rec.get("stop")), _num(rec.get("target"))
    out = {"sym": sym, "side": "short" if short else "long", "issue": rec.get("issue"), "entry": entry, "px": px,
           "notional": _notional(rec, note), "r": None, "pnl_usd": None, "to_target_pct": None}
    if px is None or entry is None:
        return out
    move = (entry - px) / entry if short else (px - entry) / entry
    if out["notional"] is not None:
        out["pnl_usd"] = out["notional"] * move
    # R 要用「最初的止损」算；台账表里的止损被改过（已经越过进场价）就不出 R，不拿别的数顶替
    if stop is not None and ((short and stop > entry) or (not short and stop < entry)):
        out["r"] = (entry - px) / (stop - entry) if short else (px - entry) / (entry - stop)
    if target is not None and px:
        out["to_target_pct"] = ((px - target) / px if short else (target - px) / px) * 100
    return out


def _cancel_why(basis):
    b = basis or ""
    if "费率" in b:
        return "funding"
    if "MA50" in b or "ma50" in b:
        return "ma50"
    if "目标" in b:
        return "target"
    if "过期" in b or "到期" in b:
        return "expire"
    return "rule"


def _recap(ledger, log, notes, prices, prev_start, prev_end):
    in_week = lambda s: prev_start <= (s or "")[:10] <= prev_end
    new, seen = [], set()
    for rec in ledger.get("rows_all", []):
        key = (rec.get("issue"), rec.get("symbol"))
        if in_week(rec.get("day")) and key not in seen:
            seen.add(key)
            new.append({"sym": rec["symbol"], "side": "short" if rec.get("side") == "做空" else "long"})
    fills = [e["标的"] for e in log if e.get("变更") == "成交" and in_week(e.get("日期"))]
    cancels = [{"sym": e["标的"], "why": _cancel_why(e.get("依据")), "expired": e.get("变更") == "过期作废"}
               for e in log if e.get("变更") in ("公开作废", "过期作废") and in_week(e.get("日期"))]
    closes = [{"sym": e["标的"], "pct": e.get("币价盈亏", ""), "usd": e.get("账户盈亏", "")}
              for e in log if e.get("变更") == "止损了结" and in_week(e.get("日期"))]
    return {"new": new, "fills": fills, "cancels": cancels, "closes": closes}


def build_pack(today, cal, ledger, log, notes, prices, px_date, rules=None):
    """today: 'YYYY-MM-DD'（北京时间的周日）。ledger: fetch_ledger.build_ledger 的结果，加 rows_all（二、三两表所有行）。
    log: 状态变更记录行。notes: {(期号, 标的): 备注}。prices: {符号: {'close': 收盘价}}，px_date: 这些收盘价的日期。"""
    rules = rules or load_rules()
    start, end = week_window(today)
    prev_end = (d(start) - timedelta(days=1)).isoformat()
    prev_start = (d(start) - timedelta(days=7)).isoformat()
    events, untested = _events(cal, start, end)
    conf, hack = _web3(cal, start, end, rules)
    warnings = []
    positions, pending = [], []
    for rec in ledger.get("active", []):
        if rec.get("status") == "持仓中":
            positions.append(_position(rec, notes.get((rec.get("issue"), rec["symbol"])), prices))
        else:
            pending.append({"sym": rec["symbol"], "side": "short" if rec.get("side") == "做空" else "long"})
    for p in positions:
        if p["px"] is None:
            warnings.append(f"{p['sym']} 没取到收盘价，浮盈不算")
    cum = ledger.get("cumulative_pnl_usd")
    equity = ledger.get("equity_usd")
    base = (equity - cum) if (equity is not None and cum is not None) else START_EQUITY_FALLBACK
    account = {"closed_n": (ledger.get("counts") or {}).get("closed"), "cum_usd": cum,
               "ret_pct": (cum / base * 100) if cum is not None and base else None}
    if account["closed_n"] is None or cum is None:
        warnings.append("台账汇总没读到，战绩数字缺失")
    floating = [p["pnl_usd"] for p in positions if p["pnl_usd"] is not None]
    recap = _recap(ledger, log, notes, prices, prev_start, prev_end)
    cal_stale = (cal.get("data_status") or {}).get("stale")
    if cal_stale:
        warnings.append("日历数据标记为过期（stale），请核对事件")
    if (cal.get("today") or "")[:10] < (d(today) - timedelta(days=2)).isoformat():
        warnings.append(f"日历数据日期是 {cal.get('today')}，比今天早两天以上")
    unl = _unlocks(cal, start, end)
    if not unl["available"]:
        warnings.append("日历里还没有代币解锁数据，海报上这一栏会写「未接入」")
    return {"kind": "weekly", "day": today, "week": {"start": start, "end": end},
            "recap_window": {"start": prev_start, "end": prev_end}, "px_date": px_date,
            "account": account, "events": events, "untested": untested, "positions": positions, "pending": pending,
            "recap": recap, "unlocks": unl, "conferences": conf, "hackathons": hack,
            "floating_usd": sum(floating) if floating else None, "warnings": warnings}


# ───────────────────────── 文案 ─────────────────────────
def _side(side, z):
    return ("空单" if side == "short" else "多单") if z else ("short" if side == "short" else "long")


def _hhmm(s):
    return s[11:16]


def _when(s, z):
    day = d(s)
    return (f"周{WD_ZH[day.weekday()]}（{md(s)}）{_hhmm(s)}" if z else f"{WD_EN[day.weekday()]} {md_en(s)}, {_hhmm(s)}")


def _usd(x, plus=True):
    return f"{'+' if x >= 0 and plus else '-' if x < 0 else ''}${abs(x):,.2f}"


def _pct(x):
    return f"{x:+.2f}%"


def _r(x):
    return f"{x:+.1f}R"


def _unlock_value(u, z):
    if u.get("usd_m") is not None:
        v = u["usd_m"]
        s = f"${v:,.0f}M" if v >= 10 else f"${v:,.1f}M"
        return f"约 {s}" if z else f"~{s}"
    if u.get("amount"):
        return u["amount"]
    return "数量待核" if z else "Amount TBC"


def _unlock_name(u, z):
    kind = u["kind_zh"] if z else u["kind_en"]
    if z:
        return f"{u['sym']} {kind}解锁" if kind else f"{u['sym']} 解锁"
    return f"{u['sym']} {kind} unlock" if kind else f"{u['sym']} unlock"


def _cancel_text(c, z):
    if c["expired"]:
        return "挂单到期没成交" if z else "the order expired unfilled"
    return {"funding": ("成交前资金费率转负，这是挂单时就写好的撤单条件", "funding turned negative before the fill, a cancel condition written when the order was posted"),
            "ma50": ("成交前日线收盘站上 MA50，这是挂单时就写好的作废条件", "the daily close got back above the 50-day average before the fill, a condition written with the order"),
            "target": ("成交前价格先跌到了目标价，这是挂单时就写好的作废条件", "price reached the target before the fill, a condition written with the order"),
            "rule": ("触发了挂单时就写好的撤单条件", "it hit a cancel condition written when the order was posted")}[c["why"]][0 if z else 1]


def view(pack, lang):
    """海报上每一块要显示的字符串。缺数据的块给 None，由海报整块隐藏或写「未接入」。"""
    z = lang == "zh"
    P = pack
    start, end = P["week"]["start"], P["week"]["end"]
    rs, re_ = P["recap_window"]["start"], P["recap_window"]["end"]
    v = {"z": z, "issue_date": P["day"]}
    v["kicker"] = (f"热币分析 · 周日 · {P['day']} · 下周清单" if z else f"Hot Coin Desk · Sunday · {P['day']} · Week Ahead")
    v["title"] = "下周清单" if z else "Week Ahead"
    v["sub"] = (f"{md(start)} 周一 → {md(end)} 周日" if z else f"Mon {md_en(start)} → Sun {md_en(end)}")
    ev = P["events"][0] if P["events"] else None
    acc = P["account"]
    # 四个数字块
    if ev:
        t = ev["time_bj"]
        v["t1v"] = f"{md(t)} {_hhmm(t)}" if z else f"{md_en(t)} {_hhmm(t)}"
        v["t1l"] = (f"{ev['title_zh']}（北京）" if z else f"{ev['title_en']} (Beijing)")
        if ev["range_typ"] is not None and ev["range_normal"] is not None:
            v["t2v"] = f"{ev['range_typ']:g}%"
            v["t2l"] = (f"公布后 2 小时一般振幅（平时 {ev['range_normal']:g}%）" if z
                        else f"Typical 2h range after release (normal {ev['range_normal']:g}%)")
        else:
            v["t2v"], v["t2l"] = "—", ("振幅统计缺失" if z else "Range stat missing")
    else:
        v["t1v"], v["t1l"] = ("无" if z else "None"), ("下周没有已检验的宏观大事件" if z else "No tested macro event next week")
        v["t2v"], v["t2l"] = "—", ("美联储 / CPI / 非农都不在下周" if z else "No FOMC / CPI / payrolls")
    v["t3v"] = _pct(acc["ret_pct"]) if acc["ret_pct"] is not None else "—"
    v["t3l"] = "账户累计收益" if z else "Account return since launch"
    fl = P["floating_usd"]
    pd_ = md(P["px_date"]) if z else md_en(P["px_date"])
    if fl is not None:
        v["t4v"] = f"{'+' if fl >= 0 else '-'}${abs(fl):.0f}"
        v["t4l"] = (f"持仓浮盈（{pd_} 收盘价）" if z else f"Open profit ({pd_} close)")
    else:
        v["t4v"] = "—"
        v["t4l"] = "当前没有持仓" if (z and not P["positions"]) else ("Nothing open" if not P["positions"] else ("浮盈未算" if z else "Open profit n/a"))
    # 大事件卡
    v["events"] = []
    for e in P["events"]:
        t = e["time_bj"]
        et = f" · 美东 {e['time_et']}" if e["time_et"] else ""
        v["events"].append({
            "h": e["title_zh"] if z else e["title_en"],
            "t": (f"北京时间 {md(t)}（周{WD_ZH[d(t).weekday()]}）{_hhmm(t)}{et}" if z
                  else f"{WD_EN[d(t).weekday()]} {md_en(t)}, {_hhmm(t)} Beijing time" + (f" · {e['time_et']}" if e["time_et"] else "")),
            "p": e["text_zh"] if z else e["text_en"]})
    v["s1"], v["s1b"] = ("下周大事件" if z else "Biggest event next week"), ("外部风险" if z else "External risk")
    v["no_event"] = ("下周没有我们检验过的宏观大事件（美联储议息、CPI、非农），不用为事件特意改动作。" if z
                     else "No macro event we have tested (Fed, CPI, payrolls) falls next week, so no need to change anything for one.")
    # 避开 / 盯住
    avoid, watch = [], []
    for e in P["events"]:
        u = e["use_zh"] if z else e["use_en"]
        if u:
            avoid.append(u)
        if e["close_bj"]:
            c = e["close_bj"]
            watch.append((f"{e['title_zh']}当天的日线收盘在 {md(c)} {_hhmm(c)}（北京时间），先看收盘，不凭一根影线下结论" if z
                          else f"{e['title_en']}: the daily close is {md_en(c)} {_hhmm(c)} Beijing time. Read the close, not a single wick"))
    if P["positions"]:
        watch.append("已在场的单子，止损按收盘价判定，盘中扫一下不算" if z else "Open positions: stops are judged on the close; an intraday poke doesn't count")
    if P["pending"]:
        syms = "、".join(p["sym"] for p in P["pending"]) if z else ", ".join(p["sym"] for p in P["pending"])
        watch.append(f"{syms} 挂单继续等价格；撤单条件（站上 MA50、先到目标、费率转负）每天复核" if z
                     else f"{syms}: orders still waiting for price; cancel conditions (back above MA50, target hit first, funding negative) are checked daily")
    v["avoid"], v["watch"] = avoid, watch
    v["s2"] = "该避开什么 · 该盯什么" if z else "What to avoid · What to watch"
    v["avoid_h"], v["watch_h"] = ("避开" if z else "Avoid"), ("盯住" if z else "Watch")
    # 解锁
    v["sc"] = "币种事件 · 解锁" if z else "Token events · Unlocks"
    unl = P["unlocks"]
    if unl["available"]:
        v["unl"] = [((md(u["date"]) if z else md_en(u["date"])), _unlock_name(u, z), _unlock_value(u, z)) for u in unl["items"]]
        v["unl_empty"] = "下周没有达到筛选标准的重要解锁。" if z else "No unlock above our filter next week."
        v["scn"] = ("解锁是潜在抛压，不是必跌信号；数量为预测值，以项目方公告为准。" if z
                    else "An unlock is potential sell pressure, not a sure drop. Amounts are forecasts; check the project's own announcement.")
    else:
        v["unl"] = None
        v["unl_empty"] = "解锁数据还没有接入，本期不列。" if z else "Unlock data is not connected yet; none listed this week."
        v["scn"] = ""
    # 行业活动
    v["s3"] = "本周行业活动" if z else "Industry events this week"
    v["conf"] = [(span_zh(c["start"], c["end"]) if z else span_en(c["start"], c["end"]),
                  c["zh"] if z else c["en"], c["city_zh"] if z else c["city_en"]) for c in P["conferences"]]
    v["hack"] = [(span_zh(h["start"], h["end"]) if z else span_en(h["start"], h["end"]),
                  (h["zh"] + (" 黑客松" if not h["online"] else " 线上黑客松")) if z else (h["en"] + (" hackathon" if not h["online"] else " hackathon (online)")))
                 for h in P["hackathons"]]
    v["hack_h"] = "线上与黑客松" if z else "Online and hackathons"
    v["s3n"] = ("会议扎堆的一周，项目方爱在这几天放消息；会议本身不是交易信号，只是消息多发期。" if z
                else "A crowded conference week means projects tend to time announcements. A conference is not a trade signal, just a busier news cycle.")
    v["s3_empty"] = "本周没有我们认得的大型会议。" if z else "No major conference we track this week."
    # 本周总结
    v["s4"] = "本周总结" if z else "Weekly recap"
    v["s4n"] = (f"{md(rs)} → {md(re_)}" if z else f"{md_en(rs)} → {md_en(re_)}")
    v["recap"] = [re.sub(r"\$([A-Za-z])", r"\1", x) for x in recap_lines(pack, lang)]
    v["foot"] = ("口径：$10,000 起始，每笔 $2,000，2 倍杠杆｜止损一律按收盘价判定｜不构成投资建议" if z
                 else "Basis: $10,000 start, $2,000 per trade, 2x leverage | Stops judged on the close | Not investment advice")
    v["src"] = ("日历数据：uncleonchain.com/calendar" if z else "Calendar: uncleonchain.com/calendar")
    if unl["available"] and unl["items"]:
        v["src"] += (" · 解锁数据：见日历页各条来源（预测值）" if z else " · Unlocks: sources on the calendar page (forecast)")
    v["sig"] = "链上大叔｜流动性到 Alpha" if z else "Onchain Uncle | Liquidity to Alpha"
    return v


def _tick(sym, z):
    return f"${sym}"


def recap_lines(pack, lang):
    z = lang == "zh"
    R, P = pack["recap"], pack
    lines = []
    if R["new"]:
        sides = {n["side"] for n in R["new"]}
        word = (_side(next(iter(sides)), z) if len(sides) == 1 else ("单" if z else "orders"))
        names = ("、" if z else ", ").join(_tick(n["sym"], z) for n in R["new"])
        lines.append(f"新增 {len(R['new'])} 张{word}：{names}" if z else f"{len(R['new'])} new {word}{'s' if len(R['new']) > 1 else ''}: {names}")
    if R["fills"]:
        names = ("、" if z else ", ").join(_tick(s, z) for s in R["fills"])
        lines.append(f"成交：{names}" if z else f"Filled: {names}")
    for c in R["cancels"]:
        lines.append(f"{_tick(c['sym'], z)} 挂单没成交就撤了：{_cancel_text(c, z)}" if z
                     else f"{_tick(c['sym'], z)} order cancelled unfilled: {_cancel_text(c, z)}")
    if P["pending"]:
        names = ("、" if z else ", ").join(_tick(p["sym"], z) for p in P["pending"])
        lines.append(f"{names} 还在等价格" if z else f"Still waiting for price: {names}")
    px_d = md(P["px_date"]) if z else md_en(P["px_date"])
    for p in P["positions"]:
        if p["px"] is None:
            continue
        bits = []
        if p["r"] is not None:
            bits.append((f"浮盈 {_r(p['r'])}" if p["r"] >= 0 else f"浮亏 {_r(p['r'])}") if z else f"{_r(p['r'])}")
        if p["pnl_usd"] is not None:
            bits.append(_usd(p["pnl_usd"]))
        head = f"{_tick(p['sym'], z)} {_side(p['side'], z)}（{px_d} 收盘价 {p['px']:g}）：" if z else f"{_tick(p['sym'], z)} {_side(p['side'], z)} (close {px_d}: {p['px']:g}): "
        tail = ""
        if p["r"] is not None and p["r"] >= 2:
            tail = "；已过 2R，按规则止损上移到进场价，最差保本" if z else "; past 2R, so by rule the stop moves to entry (worst case breakeven)"
        if p["to_target_pct"] is not None and 0 <= p["to_target_pct"] <= 2:
            tail += (f"；离目标价还差 {p['to_target_pct']:.1f}%，不提前平仓" if z else f"; {p['to_target_pct']:.1f}% from target, no early exit")
        if bits:
            lines.append(head + ("，".join(bits) if z else ", ".join(bits)) + tail)
    acc = P["account"]
    if acc["closed_n"] is not None and acc["cum_usd"] is not None and acc["ret_pct"] is not None:
        lines.append(f"已了结 {acc['closed_n']} 笔，累计 {_usd(acc['cum_usd'])}，账户收益 {_pct(acc['ret_pct'])}" if z
                     else f"{acc['closed_n']} closed trades, cumulative {_usd(acc['cum_usd'])}, account return {_pct(acc['ret_pct'])}")
    if R["closes"]:
        parts = [f"{_tick(c['sym'], z)} {c['pct']}（{c['usd']}）" for c in R["closes"]]
        lines.append(("本周平仓：" + "、".join(parts)) if z else ("Closed this week: " + ", ".join(parts)))
    else:
        lines.append("本周没有新平仓的单" if z else "Nothing closed this week")
    return lines


def _with_city(name, city, z):
    if not city:
        return name
    op, cl = ("（", "）") if z else (" (", ")")
    sep = "，" if z else ", "
    if name.endswith(cl):
        return name[:-1] + sep + city + cl
    return f"{name}{op}{city}{cl}"


def compose_posts(pack, footer_zh="", footer_en=""):
    out = {}
    for lang in ("zh", "en"):
        z = lang == "zh"
        v = view(pack, lang)
        paras = [HEADER_ZH if z else HEADER_EN]
        # 事件
        P = pack["events"]
        if P:
            e = P[0]
            t = e["time_bj"]
            use = e["use_zh"] if z else e["use_en"]
            body = (f"下周要盯的外部事件：{e['title_zh']}，北京时间{_when(t, True)}。{e['text_zh']}{use}" if z
                    else f"The one external event to watch next week: {e['title_en']}, {_when(t, False)} Beijing time. {e['text_en']} {use}")
            if e["close_bj"]:
                c = e["close_bj"]
                body += (f"真要判断方向，看事件当天的日线收盘，也就是北京时间 {md(c)} {_hhmm(c)}，别被一根影线带跑。" if z
                         else f" To read direction, wait for the daily close at {md_en(c)} {_hhmm(c)} Beijing time, not a single wick.")
            if len(P) > 1:
                more = "、".join(f"{x['title_zh']}（{_when(x['time_bj'], True)}）" for x in P[1:]) if z else ", ".join(f"{x['title_en']} ({_when(x['time_bj'], False)})" for x in P[1:])
                body += (f"另外还有{more}，处理方式一样。" if z else f" Also on the calendar: {more}. Same handling.")
            paras.append(body)
        else:
            paras.append(v["no_event"])
        # 战绩
        lines = recap_lines(pack, lang)
        sent = [x.rstrip("。.；;") for x in lines]
        recap_body = ("本周战绩：" + "。".join(sent) + "。") if z else ("Last week's scorecard: " + ". ".join(x[:1].upper() + x[1:] for x in sent) + ".")
        paras.append(recap_body)
        paras.append("止损一律按收盘价判定，盘中影线扫到不算。如果你在交易所挂的是盘中触发的止损，结果会和我们不一样，止损要给自己留更大的空间。" if z
                     else "We judge stops on the daily close; an intraday wick doesn't count. If your exchange stop triggers intraday, your result will differ from ours, so leave yourself more room.")
        # 解锁
        if v["unl"] is None:
            pass   # 数据没接入：帖子里不提，不能让读者以为下周没有解锁
        elif v["unl"]:
            items = [f"{n}（{val}，{dd}）" for dd, n, val in v["unl"]] if z else [f"{n} ({val}, {dd})" for dd, n, val in v["unl"]]
            paras.append(("币种事件：" + "；".join(items) + "。解锁是潜在抛压，不是必跌信号，数量以项目方公告为准。") if z
                         else ("Token events: " + "; ".join(items) + ". An unlock is potential sell pressure, not a sure drop; check the project's own announcement."))
        else:
            paras.append("币种事件：下周没有达到我们筛选标准的重要解锁。" if z else "Token events: no unlock above our filter next week.")
        # 行业活动
        if v["conf"]:
            names = ("、" if z else ", ").join(_with_city(n, city, z) for dd, n, city in v["conf"][:4])
            paras.append((f"本周行业活动：{names}。会议扎堆的周，项目方爱放消息，但会议本身不是交易信号。") if z
                         else (f"Industry events: {names}. Projects like to time news around conferences, but a conference is not a trade signal."))
        foot = footer_zh if z else footer_en
        if foot:
            paras.append(foot)
        out[lang] = limit_cashtags("\n\n".join(paras))
    return out["zh"], out["en"]
