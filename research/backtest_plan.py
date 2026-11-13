#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日交易计划 · 历史回测（机械部分）：币安永续日线 + L1/L2 历史读数 + 实盘同一套卡片/止损/4R/跟踪/熔断规则。

用法（在装好 requests 的机器上，能连币安；Mac 要开代理时设 HOTCOIN_HTTP_PROXY=http://127.0.0.1:7890）：
    cd hotcoin-trade-automation && git pull && python3 research/backtest_plan.py
输出在 research/backtest_out/：summary.txt（月度、年度盈亏）、trades_*.csv（每笔明细）。
再跑一次会复用 research/backtest_cache/ 里已拉的数据。

只回测程序能算的部分：
  · 机会卡：日线接触段位置（与线上同一个 levels()）、做多（价在MA200上、支撑在MA200上、距现价≤6%、顺势买回踩）、
    做空（线上同一个 做空筛选，路B需人工核实的不开）；止损 max(0.5×ATR14,2%) 收盘价；4R 目标；2R 保本；3R 跟 MA20；20 天到期；来回费 0.1%
  · 仓位：2000×方向倍数×15天内碰过(1/3)×L1折扣(0.7)×L2熊市(0.5，仅多)；名义≤20000；主流币同向≤3；熔断同线上
  · L1：净流动性/实际利率折扣，数据不全不开新单；L2：顶部风险区不开多、熊市多单减半；
    宏观闸门：BTC 涨腿>89天且腿内回撤<10% / BTC 在 MA20 下且7日跌>5% 不开新多
不回测（程序算不了，会让结果和实盘有差距）：热币叙事选币、非农/FOMC/CPI 事件窗口和48小时二元事件、人工判断的否决、盘中先后顺序（同一根线按保守算）。
对照组「无宏观」= 同样的卡片，但关掉 L1/L2/闸门，用来看 L1–L4 到底加了多少分。
"""
import bisect
import csv
import importlib.util
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "research" / "backtest_cache"
OUT = ROOT / "research" / "backtest_out"
VENDOR = ROOT / "pipeline" / "vendor"
sys.path.insert(0, str(ROOT / "host"))

DAY = 86400000
TRADABLE = ["BTC", "ETH", "SOL", "XRP", "BNB", "UNI", "CAKE", "HYPE", "ENA", "TAO", "RAY", "DOGE", "LDO", "ARB", "NEAR",
            "ZEC", "JUP", "AAVE", "WLD", "SUI", "INJ"]
CORE = ["BTC", "ETH", "SOL", "XRP", "BNB"]
RAW = "https://raw.githubusercontent.com/ivonasmith/uncleonchain/main/"
SERIES = ["netliq13", "usd13", "real13", "baa13", "nfci13", "optimism_z"]
NETLIQ_TH, REAL_TH = -2.72, 0.40
BASE_USD, MAX_NOTIONAL, MAX_MAJOR_SIDE, MAX_DIST = 2000, 20000, 3, 6.0
STATE_NAME = {"BULL": "牛市", "BULL_RISK": "牛市·顶部风险区", "BULL_PULLBACK": "牛市回撤·待确认", "BEAR": "熊市",
              "BEAR_ZONE": "熊底区", "RECOVERY": "熊末→牛初"}


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------- 取数（带缓存） ----------------
def cached(name, fn):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    data = fn()
    if data is not None:
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def http_json(url):
    import requests
    proxy = os.environ.get("HOTCOIN_HTTP_PROXY")
    r = requests.get(url, timeout=60, proxies={"http": proxy, "https": proxy} if proxy else None)
    r.raise_for_status()
    return r.json()


def pull_klines(pull, sym):
    k = pull.klines(sym + "USDT", "1d", 3000)
    return k


def pull_funding(pull, sym):
    out, start = [], 0
    while True:
        rows = pull.get("/fapi/v1/fundingRate", {"symbol": sym + "USDT", "startTime": start, "limit": 1000})
        if not rows:
            break
        out += [[int(r["fundingTime"]), float(r["fundingRate"])] for r in rows]
        if len(rows) < 1000:
            break
        start = int(rows[-1]["fundingTime"]) + 1
        time.sleep(0.3)
    return out


def to_date(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def series_asof(dates, values):
    keys = [datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000 for d in dates]

    def get(ms):
        i = bisect.bisect_right(keys, ms) - 1
        if i < 0 or ms - keys[i] > 14 * DAY:     # 超过两周没更新，当作没有
            return None
        return values[i]
    return get


# ---------------- 单币：每天的卡片原料（可并行） ----------------
def coin_days(args):
    sym, kl, fund = args
    ind = load_mod(VENDOR / "指标与关键位.py", "ind")
    scr = load_mod(VENDOR / "做空筛选.py", "scr")
    bars = [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5]),
                 ct=int(k[6])) for k in kl]
    fts = [x[0] for x in fund]
    frs = [x[1] for x in fund]

    def fund_avg(now, days):
        lo = bisect.bisect_right(fts, now - days * DAY)
        hi = bisect.bisect_right(fts, now)
        seg = frs[lo:hi]
        return None if len(seg) < days else statistics.mean(seg) * 3 * 365 * 100

    days = []
    for i in range(len(bars)):
        if i < 250:          # 慢档（≥250天）才有卡片
            continue
        w = bars[: i + 1]
        cl = [b["c"] for b in w]
        price = cl[-1]
        m200 = ind.ma(cl, 200)
        atr_d = ind.atr(w)
        if m200 is None or atr_d is None:
            continue
        high120 = max(b["h"] for b in w[-120:])
        m = dict(price=price, ma20=ind.ma(cl, 20), ma50=ind.ma(cl, 50), ma200=m200,
                 dev200=round((price / m200 - 1) * 100, 1), atr_d_pct=atr_d / price * 100,
                 rsi_d=ind.rsi(cl), 阶段=ind.stage(price, m200, price / high120 - 1),
                 fund_ann=None, fund_30d_ann=fund_avg(w[-1]["ct"], 30), fund_7d_ann=fund_avg(w[-1]["ct"], 7))
        want_long = price > m200
        want_short = m["阶段"] != "主升浪(禁止做空)" and m["阶段"] != "数据不足"
        if not (want_long or want_short):
            continue
        lv = ind.levels(w[-1600:], price)
        below = [x for x in lv if -12 < x["dist"] < 0]
        above = [x for x in lv if 0 < x["dist"] < 14]
        m["d1_above"] = above
        d = {"i": i, "ct": bars[i]["ct"]}
        if want_long and below:
            lvl = min(below, key=lambda x: abs(x["dist"]))
            if lvl["mid"] >= m200 and abs(lvl["dist"]) <= MAX_DIST:
                d["long"] = {"entry": lvl["mid"], "gap": lvl["gap"], "segs": lvl["segs"], "atr": m["atr_d_pct"]}
        if want_short and above:
            res = scr.扫一个币(sym, m, verbose=False)
            cand = res.get("候选") or []
            near = min(above, key=lambda x: x["dist"])
            hit = [c for c in cand if abs(c["进场"] / near["mid"] - 1) < 0.0005]
            if hit and not res.get("费率否决") and not (isinstance(res.get("门③需要人工核实"), str) and res["门③需要人工核实"]):
                d["short"] = {"entry": near["mid"], "gap": near["gap"], "segs": near["segs"], "atr": m["atr_d_pct"],
                              "touched": hit[0]["15天内碰过"], "path": res.get("门③路径") or "MA200下方"}
        if "long" in d or "short" in d:
            days.append(d)
    return sym, bars, days


# ---------------- 宏观读数 ----------------
class Macro:
    def __init__(self):
        self.l1 = {}
        for name in SERIES:
            d = cached(f"{name}.json", lambda n=name: http_json(RAW + f"site/data/series/{n}.json"))
            self.l1[name] = series_asof(d["dates"], d["values"])
        l2 = cached("l2_series.json", lambda: http_json(RAW + "data/l2/l2_series.json"))
        self.l2_start = datetime.strptime(l2["start"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000
        self.l2_states = [l2["state_codes"][x] if x is not None else None for x in l2["series"]["state"]]
        leg = cached("leg_history.json", lambda: http_json(RAW + "data/leg/leg_history.json"))
        self.legs = [x for x in leg["legs"] if x.get("confirm_date")]
        self.leg_confirm = [x["confirm_date"] for x in self.legs]
        px = cached("btc_price.json", lambda: http_json(RAW + "site/data/series/btc_price.json"))
        self.px_start = datetime.strptime(px["start"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000
        self.px = px["values"]

    def l2(self, ms):
        i = int((ms - self.l2_start) // DAY)
        return self.l2_states[i] if 0 <= i < len(self.l2_states) else None

    def l1_mult(self, ms):
        v = {n: self.l1[n](ms) for n in SERIES}
        complete = all(x is not None for x in v.values())
        if not complete:
            return False, 1.0, v
        net = v["netliq13"] <= NETLIQ_TH and v["usd13"] > 0
        real = v["real13"] >= REAL_TH and (v["baa13"] > 0 or v["nfci13"] > 0 or v["optimism_z"] < 0)
        return True, (0.7 if (net or real) else 1.0), v

    def btc_close(self, ms):
        i = int((ms - self.px_start) // DAY)
        return self.px[i] if 0 <= i < len(self.px) else None

    def up_leg_locked(self, ms):
        """BTC 涨腿超过 89 天，且腿内收盘最大回撤 <10% → 不开新多。腿按“确认日”才算已知，不偷看未来。"""
        day = to_date(ms)
        j = bisect.bisect_right(self.leg_confirm, day) - 1
        if j < 0:
            return False
        leg = self.legs[j]
        if leg["dir"] != "up":
            return False
        s = int((datetime.strptime(leg["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000
                 - self.px_start) // DAY)
        e = int((ms - self.px_start) // DAY)
        if e - s <= 89:
            return False
        peak, mdd = 0, 0
        for c in self.px[s:e + 1]:
            peak = max(peak, c)
            mdd = max(mdd, (peak - c) / peak * 100)
        return mdd < 10.0


# ---------------- 组合回放 ----------------
def run(data, macro, paper, use_macro):
    all_dates = sorted({d["ct"] for sym in data for d in data[sym]["days"]})
    bars_by = {s: data[s]["bars"] for s in data}
    idx_by = {s: {b["ct"]: k for k, b in enumerate(bars_by[s])} for s in data}
    days_by = {s: {d["ct"]: d for d in data[s]["days"]} for s in data}
    btc = bars_by.get("BTC")
    btc_idx = idx_by.get("BTC", {})
    trades, open_pos, state = [], {}, {"trades": [], "baseline_ts": 0, "halt": None}
    order = [s for s in CORE if s in data] + [s for s in data if s not in CORE]
    for ct in all_dates:
        # 已了结的单进熔断账本
        for t in trades:
            if t["status"] == "closed" and t["exit"]["ts"] <= ct and not t.get("_in"):
                t["_in"] = True
                state["trades"].append(t)
        for s in [s for s, t in open_pos.items() if t["status"] == "closed" and t["exit"]["ts"] <= ct]:
            del open_pos[s]
        rs = paper.risk_state(state, ct)
        if rs.get("halted"):
            continue
        if rs.get("reason"):
            state["halt"] = {"until": ct + 3 * DAY, "reason": rs["reason"]}
            state["baseline_ts"] = ct + 3 * DAY
            continue
        half = 0.5 if rs.get("half") else 1.0
        ok1, l1m, _ = macro.l1_mult(ct)
        st = macro.l2(ct)
        gate_long_ok = True
        if use_macro:
            if not ok1:
                gate_long_ok = False
            if st == "BULL_RISK":
                gate_long_ok = False
            if macro.up_leg_locked(ct):
                gate_long_ok = False
            if btc and ct in btc_idx:
                k = btc_idx[ct]
                cl = [b["c"] for b in btc[: k + 1]]
                if k >= 20 and cl[-1] < sum(cl[-20:]) / 20 and k >= 7 and cl[-1] / cl[-8] - 1 < -0.05:
                    gate_long_ok = False
        for s in order:
            d = days_by[s].get(ct)
            if not d or s in open_pos:
                continue
            for side in ("long", "short"):
                card = d.get(side)
                if not card:
                    continue
                if side == "long" and not gate_long_ok:
                    continue
                if side == "short" and use_macro and not ok1:
                    continue
                k = idx_by[s][ct]
                if k + 1 >= len(bars_by[s]):
                    continue
                nb = bars_by[s][k + 1]
                entry = card["entry"]
                if side == "long" and not nb["l"] <= entry:
                    continue
                if side == "short" and not nb["h"] >= entry:
                    continue
                sp = max(0.5 * card["atr"], 2.0) / 100
                is_long = side == "long"
                stop0 = entry * (1 - sp) if is_long else entry * (1 + sp)
                target = entry * (1 + 4 * sp) if is_long else entry * (1 - 4 * sp)
                touched = card["gap"] <= 15 if is_long else card.get("touched")
                size = (1.0 if is_long else 0.5) * (1 / 3 if touched else 1.0)
                if use_macro:
                    size *= l1m
                    if is_long and st == "BEAR":
                        size *= 0.5
                size *= half
                notional = round(BASE_USD * size)
                live = [t for t in open_pos.values() if t["status"] != "closed" or t["exit"]["ts"] > nb["t"]]
                if sum(t["notional_usd"] for t in live) + notional > MAX_NOTIONAL:
                    continue
                if s in CORE and sum(1 for t in live if t["sym"] in CORE and t["side"] == ("多" if is_long else "空")
                                     and not paper.is_locked(t)) >= MAX_MAJOR_SIDE:
                    continue
                trade = {"sym": s, "side": "多" if is_long else "空", "entry": entry, "stop0": stop0, "target": target,
                         "notional_usd": notional, "ts": nb["t"], "paper": True}
                bars = [{"high": b["h"], "low": b["l"], "close": b["c"], "close_time": b["ct"]}
                        for b in bars_by[s][k + 1:]]
                # 拿到 MA20 跟踪所需的前 19 根：simulate 用 bars 内的序号，所以带上前面 19 根（它们的 close_time<=ts 会被跳过）
                pre = [{"high": b["h"], "low": b["l"], "close": b["c"], "close_time": b["ct"]}
                       for b in bars_by[s][max(0, k - 19):k + 1]]
                res = paper.simulate(trade, pre + bars, 10 ** 15)
                trade.update(res)
                trade["day"] = to_date(nb["t"])
                trade["L2"] = STATE_NAME.get(st, "?")
                trade["L1x"] = l1m
                trade["path"] = card.get("path", "")
                if trade["status"] == "closed":
                    trade["exit_day"] = to_date(trade["exit"]["ts"])
                trades.append(trade)
                open_pos[s] = trade
                break      # 同一个币同一天只留一张
    return trades


# ---------------- 报告 ----------------
def stats(rows):
    if not rows:
        return "0笔"
    pnl = [t["pnl_usd"] for t in rows]
    win = sum(1 for p in pnl if p > 0)
    r = [t["r"] for t in rows]
    return f"{len(rows)}笔 胜率{win / len(rows) * 100:.0f}% 平均{statistics.mean(r):+.2f}R 合计{sum(pnl):+,.0f}美元"


def maxdd(rows):
    cum = peak = dd = 0.0
    for t in sorted(rows, key=lambda t: t["exit"]["ts"]):
        cum += t["pnl_usd"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def report(name, trades, lines):
    closed = [t for t in trades if t["status"] == "closed"]
    openn = [t for t in trades if t["status"] != "closed"]
    lines.append(f"\n===== {name} =====")
    lines.append(f"全部：{stats(closed)}；最大回撤 {maxdd(closed):,.0f} 美元（占账户1万的 {maxdd(closed) / 100:.1f}%）；到最后还没了结 {len(openn)} 笔（未计入）")
    lines.append(f"做多：{stats([t for t in closed if t['side'] == '多'])}")
    lines.append(f"做空：{stats([t for t in closed if t['side'] == '空'])}")
    ym = defaultdict(float)
    yy = defaultdict(float)
    yn = defaultdict(int)
    for t in closed:
        ym[t["exit_day"][:7]] += t["pnl_usd"]
        yy[t["exit_day"][:4]] += t["pnl_usd"]
        yn[t["exit_day"][:4]] += 1
    lines.append("— 年度（按了结日；占账户1万的比例，不复利）—")
    for y in sorted(yy):
        lines.append(f"  {y}：{yy[y]:+,.0f} 美元 ({yy[y] / 100:+.1f}%) {yn[y]}笔")
    lines.append("— 月度（美元；行=年，列=1~12月）—")
    for y in sorted(yy):
        cells = [f"{ym.get(f'{y}-{m:02d}', 0):+7,.0f}" if f"{y}-{m:02d}" in ym else "      ." for m in range(1, 13)]
        lines.append(f"  {y} " + " ".join(cells))
    lines.append("— 按开仓时 L2 状态 —")
    by = defaultdict(list)
    for t in closed:
        by[t["L2"]].append(t)
    for k, v in by.items():
        lines.append(f"  {k}：{stats(v)}")
    lines.append(f"— L1 折扣期开的单 ×0.7：{stats([t for t in closed if t['L1x'] < 1])}；没折扣：{stats([t for t in closed if t['L1x'] >= 1])}")
    return closed


def write_csv(name, trades):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"trades_{name}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["币", "方向", "开仓日", "了结日", "了结方式", "进场", "止损", "目标", "名义美元", "R", "盈亏美元", "最大浮盈R",
                    "持有天数", "L2状态", "L1倍数", "做空路径"])
        for t in trades:
            if t["status"] != "closed":
                continue
            w.writerow([t["sym"], t["side"], t["day"], t["exit_day"], t["exit"]["reason"], t["entry"], t["stop0"],
                        t["target"], t["notional_usd"], t["r"], t["pnl_usd"], t["mfe_r"], t["days_held"], t["L2"],
                        t["L1x"], t["path"]])


def main():
    pull = load_mod(VENDOR / "拉数据.py", "pull")
    paper = __import__("paper")
    only = sys.argv[1:] or TRADABLE
    jobs, got = [], {}
    for s in only:
        kl = cached(f"k_{s}.json", lambda s=s: pull_klines(pull, s))
        if not kl or len(kl) < 300:
            print(f"{s}：币安永续日线不足（{len(kl) if kl else 0}），跳过")
            continue
        fd = cached(f"f_{s}.json", lambda s=s: pull_funding(pull, s)) or []
        got[s] = (to_date(int(kl[0][0])), len(kl))
        jobs.append((s, kl, fd))
    print("数据：", "、".join(f"{s}({v[0]}起)" for s, v in got.items()), flush=True)
    with Pool(min(4, os.cpu_count() or 1)) as pool:
        res = pool.map(coin_days, jobs)
    data = {s: {"bars": bars, "days": days} for s, bars, days in res}
    macro = Macro()
    lines = [f"回测区间：{min(v[0] for v in got.values())} ~ {to_date(max(b['ct'] for s in data for b in data[s]['bars']))}",
             "币种及币安永续日线起点：" + "、".join(f"{s}{v[0]}" for s, v in got.items()),
             "说明：每币要先有250天日线才出卡片；L1 周度读数自2013年底起、L2 状态自2010起，都按当日已知读取。"]
    for use, name in ((True, "full"), (False, "nomacro")):
        trades = run(data, macro, paper, use)
        closed = report("完整系统（含 L1/L2/闸门）" if use else "对照：同样的卡片，不用 L1/L2/闸门", trades, lines)
        write_csv(name, trades)
    OUT.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    (OUT / "summary.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
