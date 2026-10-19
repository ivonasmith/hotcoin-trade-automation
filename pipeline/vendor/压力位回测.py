#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""压力位历史胜率回测 —— 给 指标与关键位.py 找出来的每个"接触段位置"，
补一个"历史上真拿它做过空/多，赚不赚"的统计，不只是"存在过、碰过几次"。

跟 做空筛选.py 的关系：做空筛选只看"现在这一档"够不够格；这个脚本回答
"这一档过去被测试的时候，historically 后续10/20天平均表现如何"，
是做空筛选③"需要人工核实"那一步的辅助证据，不能代替基本面理由本身。

数据源：直接复用 拉数据.py 里已经验证过的网络层（代理/主备域名/重试），
不新开一套网络配置，你的FlClash环境不用再调一次。

用法（一般由 每日取数.py 自动调用，不用单独跑）：
    python3 压力位回测.py SOL ETH XRP
    python3 压力位回测.py SOL --价位 120,124.99      # 直接测手动指定的价位

⚠️ 拉的是币安USDT本位永续合约K线（跟拉数据.py同一个数据源），
   不是币安现货——期限较新的合约（比如HYPE）历史天然短，回测样本量会小，
   这是数据源限制，不是脚本bug。
"""
import json, os, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 拉数据  # 复用它的 get()/klines()，代理和重试都不用重写


def _to_bars(raw_klines):
    """拉数据.klines() 返回的是原始币安K线（字符串），转成 (ts,o,h,l,c,v) 浮点tuple。"""
    if not raw_klines:
        return []
    return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in raw_klines]


def find_flip_zones(bars, tol=0.015, min_touches=8, min_dominant_ratio=1.8, min_age_days=120):
    closes = [b[4] for b in bars]; n = len(closes)
    zones, used = [], [False]*n
    for i in range(n):
        if used[i]: continue
        base = closes[i]
        idxs = [j for j in range(n) if abs(closes[j]-base)/base <= tol]
        if len(idxs) < min_touches: continue
        for j in idxs: used[j] = True
        mid = sum(closes[j] for j in idxs)/len(idxs)
        first_ts, last_ts = bars[idxs[0]][0], bars[idxs[-1]][0]
        age_days = (last_ts-first_ts)/86400000
        if age_days < min_age_days: continue
        from_above = sum(1 for j in idxs if j>0 and closes[j-1] > mid*(1+tol/2))
        from_below = sum(1 for j in idxs if j>0 and closes[j-1] < mid*(1-tol/2))
        dominant = "支撑为主" if from_above>=from_below else "压力为主"
        if max(from_above,from_below)/max(min(from_above,from_below),1) < min_dominant_ratio:
            dominant = "支撑压力均衡"
        zones.append({"mid": round(mid,8), "touches": len(idxs), "支撑测试次数": from_above,
                      "压力测试次数": from_below, "定性": dominant, "age_days": round(age_days,1)})
    zones.sort(key=lambda z: -z["touches"])
    return zones


def test_zone_retests_from_below(bars, mid, horizon=(10,20), tol=0.02):
    """只统计"从下方回踩、当天没破前高"的真正压力测试事件——当天创新高的是突破，不是测压力，单独记。"""
    tested, breakouts = [], []
    for i in range(120, len(bars)-max(horizon)):
        c = bars[i][4]; prev_c = bars[i-1][4]
        if not (prev_c < mid*(1-tol) and abs(c-mid)/mid <= tol): continue
        high120 = max(b[2] for b in bars[i-120:i])
        date = datetime.datetime.fromtimestamp(bars[i][0]/1000, datetime.timezone.utc).date().isoformat()
        if c > high120:
            breakouts.append({"date": date, "price": c}); continue
        fwd = {h: round((min(b[4] for b in bars[i:i+h+1])-c)/c*100,2) for h in horizon}
        tested.append({"date": date, "price": c, "距120日高点_%": round((c-high120)/high120*100,2),
                       **{f"fwd_{h}d_%":fwd[h] for h in horizon}})
    return tested, breakouts


def test_manual_levels(bars, levels, tol=0.02, horizon=(10,20)):
    out = []
    for level in levels:
        from_above_hits, from_below_tested, from_below_breakout = [], [], []
        for i in range(120, len(bars)-max(horizon)):
            c = bars[i][4]; prev_c = bars[i-1][4]
            if abs(c-level)/level > tol: continue
            date = datetime.datetime.fromtimestamp(bars[i][0]/1000, datetime.timezone.utc).date().isoformat()
            high120 = max(b[2] for b in bars[i-120:i])
            if prev_c > level*(1+tol/2):
                fwd = {h: round((min(b[4] for b in bars[i:i+h+1])-c)/c*100,2) for h in horizon}
                from_above_hits.append({"date":date,"price":c,**{f"fwd_{h}d_%":fwd[h] for h in horizon}})
            elif prev_c < level*(1-tol/2):
                if c > high120:
                    from_below_breakout.append({"date":date,"price":c,"note":"当天创120日新高，是突破不是测压力"})
                    continue
                fwd = {h: round((min(b[4] for b in bars[i:i+h+1])-c)/c*100,2) for h in horizon}
                from_below_tested.append({"date":date,"price":c,**{f"fwd_{h}d_%":fwd[h] for h in horizon}})
        out.append({"价位": level, "测支撑次数": len(from_above_hits), "测支撑明细": from_above_hits,
                    "测压力次数(不含突破)": len(from_below_tested), "测压力明细": from_below_tested,
                    "同价位突破次数": len(from_below_breakout), "突破明细": from_below_breakout})
    return out


def run(symbols, manual_symbol=None, manual_levels=None, max_bars=3000, top_n_zones=6, verbose=True):
    """核心入口，供 每日取数.py 直接调用。symbols 传币种代码（不带USDT），
    比如 ["SOL","ETH"]，跟 指标.json 的 key 保持一致。"""
    out = {}
    for sym in symbols:
        pair = sym.upper() + "USDT"
        if verbose: print(f"  压力位回测：拉取 {pair} 历史K线...")
        raw = 拉数据.klines(pair, "1d", max_bars)
        bars = _to_bars(raw)
        if len(bars) < 150:
            out[sym] = {"error": f"历史不足150天（拿到{len(bars)}根），跳过"}
            continue
        cur_price = bars[-1][4]
        cur_high120 = max(b[2] for b in bars[-120:])
        zones = find_flip_zones(bars)[:top_n_zones]
        zlist = []
        for z in zones:
            tested, breakouts = test_zone_retests_from_below(bars, z["mid"])
            dist_now = round((cur_price - z["mid"])/z["mid"]*100, 2)
            zlist.append({"zone": z, "现价距此_%": dist_now, "回踩测试次数": len(tested),
                          "突破次数": len(breakouts), "回踩测试明细": tested})
        sym_out = {"现价": cur_price, "距120日高点_%": round((cur_price-cur_high120)/cur_high120*100,2),
                  "zones": zlist}
        if manual_symbol and sym.upper() == manual_symbol.upper() and manual_levels:
            sym_out["手动指定价位测试"] = test_manual_levels(bars, manual_levels)
        out[sym] = sym_out
        time.sleep(0.3)
    return out


def main():
    argv = sys.argv[1:]
    manual_symbol, manual_levels = None, None
    if "--价位" in argv:
        idx = argv.index("--价位")
        manual_levels = [float(x) for x in argv[idx+1].split(",")]
        manual_symbol = argv[idx-1] if idx > 0 else None
        argv = argv[:max(idx-1,0)]
    symbols = argv or ["BTC","SOL","ETH"]
    result = run(symbols, manual_symbol, manual_levels, verbose=True)
    out_path = os.path.join(HERE, f"压力位回测_{datetime.date.today().isoformat()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n已存到 {out_path}")


if __name__ == "__main__":
    main()
