#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做空候选筛选 —— 复用接触段找位的输出，把「压力位」当成做空候选位。

规矩来自 热币分析操作规格-v2.html「做空」条目（2026-09-10 落成代码）：
  不设默认不做空，数据支持就空。但五条都要过：
  ①距120日高点回撤 <5%（主升浪）→ 硬性禁止，无例外
  ②「跌破MA200 + 仍超买」的反模式不碰
  ③MA200上方要空，两条路二选一（v6.3，2026-10-12 回测后修订）：
     路A「趋势已破」：收盘同时跌破MA20和MA50，且近7日平均资金费率不为负 → 代码放行，判断级、小仓位
        （回测：790个独立事件，20日相对BTC +4.7%，90%区间[+2.1%,+7.1%]，三个年代同向，模拟R 0.17）
     路B「具体基本面利空」：代码给不出答案，只标记「需要人工核实」，逼着写手真正想一遍
     「涨太多了」（30日涨幅大但趋势没破）两条路都不算——回测里这一组相对BTC -3.2%、模拟R 0.01，没有优势
  ④资金费率（v6.4，2026-10-13）：高费率不预示回调，只作温度描述，不再当做空佐证；
     费率30日均（年化）<0 → 不开新空（任何路径，包括MA200下方），L4研究 已验证·方向
     （字段 fund_30d_ann，指标.json 暂时没有这一列，没有时只提示「无数据」，不否决）
  ⑤必须是接触段算法给出的具体一档，不许「逢高空」

仓位：做空一律方向倍数0.5（每笔投入砍半），跟仓位倍数（刚被碰过=1/3）各自独立相乘。

用法
  python3 做空筛选.py            # 扫 指标.json 里全部币种
  python3 做空筛选.py BTC ETH    # 只看指定币种
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def calc_stop_target(entry, atr_pct, floor=2.0):
    """跟做多用同一套 0.5×ATR14（慢档下限2%）+ 4R，只是方向反过来：
    止损在进场价上方，目标在进场价下方。"""
    stop_pct = max(0.5 * atr_pct, floor) / 100
    stop = entry * (1 + stop_pct)
    R = stop - entry
    target = entry - 4 * R
    return round(stop, 6), round(R, 6), round(target, 6)


def 资金费率读法(fund_ann):
    if fund_ann is None:
        return "无数据"
    if fund_ann < 0:
        return f"{fund_ann:.1f}%（转负=空头拥挤，有轧空风险，不利于新开空）"
    if fund_ann >= 30:
        return f"{fund_ann:.1f}%（偏热，只作温度描述：高费率不预示回调，不构成做空佐证）"
    if fund_ann >= 5:
        return f"{fund_ann:.1f}%（正常区间，温度描述）"
    return f"{fund_ann:.1f}%（偏低，温度描述）"


def 扫一个币(sym, m, verbose=True):
    """verbose=True 时照旧打印终端报告；不管 verbose 是什么，都返回结构化结果，
    供 每日取数.py 直接调用合并进日常输出，不用重新跑一遍逻辑。"""
    结果 = {"标的": sym, "现价": m.get("price"), "阶段": m.get("阶段", "数据不足"),
           "门①": None, "门②": None, "门③需要人工核实": None, "资金费率佐证": None,
           "候选": []}
    price = m.get("price")
    阶段 = m.get("阶段", "数据不足")
    ma200 = m.get("ma200")
    dev200 = m.get("dev200")
    rsi = m.get("rsi_d")
    fund = m.get("fund_ann")
    fund30 = m.get("fund_30d_ann")   # v6.4：费率30日均（年化），指标.json 补上这一列后自动生效
    fund7 = m.get("fund_7d_ann")
    候选levels = m.get("d1_above") or []

    if verbose:
        print("=" * 78)
        print(f"{sym}  现价 {price:,.6g}  阶段：{阶段}")

    if 阶段 == "主升浪(禁止做空)":
        结果["门①"] = "未过：主升浪，硬性禁止做空"
        if verbose: print("  ❌ 门①未过：主升浪，硬性禁止做空，本币种今天不看候选位")
        return 结果
    if 阶段 == "数据不足":
        结果["门①"] = "数据不足，算不出阶段"
        if verbose: print("  ⚠️ 数据不足，算不出阶段，跳过")
        return 结果
    结果["门①"] = "过：不在主升浪区间"
    if verbose: print("  ✓ 门①过：不在主升浪区间，做空理论上可以考虑")

    below_ma200 = ma200 and price < ma200
    if below_ma200 and rsi and rsi > 70:
        结果["门②"] = f"撞上反模式：MA200下方（偏离{dev200:+.1f}%）且RSI仍{rsi:.1f}超买"
        if verbose:
            print(f"  ❌ 门②撞上反模式：价格在MA200下方（偏离{dev200:+.1f}%）且RSI仍{rsi:.1f}超买——"
                  f"这个形态历史实测最差，不做")
        return 结果
    结果["门②"] = "过：不是反模式"
    if verbose: print("  ✓ 门②过：不是「跌破MA200+仍超买」这个反模式")

    # v6.4：费率30日均<0 → 不开新空（任何路径）。L4研究：已验证·方向（31次，30天上涨74%对58%）
    结果["费率30日均"] = fund30
    结果["费率7日均"] = fund7
    if fund30 is not None and fund30 < 0:
        结果["费率否决"] = True
        结果["门③路径"] = "费率30日均为负→不开新空（v6.4，已验证·方向）"
        结果["资金费率佐证"] = 资金费率读法(fund)
        if verbose:
            print(f"  ⛔ v6.4：费率30日均{fund30:.1f}%为负（空头拥挤，之后一两个月偏强）——不开新空，今天不看候选位")
        return 结果
    if fund30 is None and verbose:
        print("  ℹ️ 费率30日均：指标.json 暂无 fund_30d_ann 字段，本次只按当前费率和近7天口径判断（v6.4 的30日均否决未生效）")

    above_ma200 = ma200 and price >= ma200
    ma20, ma50 = m.get("ma20"), m.get("ma50")
    趋势已破 = bool(ma20 and ma50 and price < ma20 and price < ma50)
    结果["趋势已破"] = 趋势已破
    结果["门③路径"] = None
    结果["费率否决"] = False
    if above_ma200:
        if 趋势已破 and (fund7 is None or fund7 < 0):
            # 路径A的回测口径是近7日平均费率；缺失或为负都不能放行。
            原因 = "近7日平均费率缺失" if fund7 is None else f"近7日平均费率{fund7:.1f}%为负"
            结果["门③路径"] = f"趋势已破，但{原因}→否决"
            结果["门③需要人工核实"] = f"趋势已破（价格低于MA20和MA50）但{原因}：路径A条件不完整，不新开"
            结果["费率否决"] = True
            结果["资金费率佐证"] = 资金费率读法(fund)
            if verbose:
                print(f"  ⛔ 门③：趋势已破，但{原因}——路径A要求近7日平均费率不为负，不新开")
            return 结果
        if 趋势已破:
            结果["门③路径"] = "路A：趋势已破（判断级，小仓位）"
            结果["门③需要人工核实"] = False
            结果["门③备注"] = "费率使用近7日平均年化；今日日线未收盘时，趋势已破以收盘为准"
            if verbose:
                print(f"  ✓ 门③路A过：价格在MA200上方（偏离{dev200:+.1f}%），但已同时跌破MA20和MA50，"
                      f"近7日平均资金费率{fund7:.1f}%不为负。判断级，小仓位，收盘确认为准")
        else:
            结果["门③路径"] = "路B：需要具体基本面利空"
            结果["门③需要人工核实"] = f"价格在MA200上方（偏离{dev200:+.1f}%），趋势未破（仍在MA20或MA50上方），必须配具体基本面利空，不能只写涨太多了"
            if verbose:
                print(f"  ⚠️ 门③需要人工核实：价格在MA200上方（偏离{dev200:+.1f}%），趋势未破，"
                      f"必须配一条具体的基本面利空，不能只写「涨太多了」（回测里这类没有优势）。"
                      f"资金流/情绪面背景信息（ETF流出、巨鲸开空）不够格，只能当佐证")
    else:
        结果["门③需要人工核实"] = False
        if verbose: print("  ✓ 门③不适用：价格已在MA200下方，不受「必须有基本面理由」这条限制")

    结果["资金费率佐证"] = 资金费率读法(fund)
    if verbose: print(f"  资金费率佐证：{结果['资金费率佐证']}")

    if not 候选levels:
        if verbose: print("  没有找到压力位候选——没有位置就不做，不写「逢高空」")
        return 结果

    if verbose: print(f"  候选压力位（门⑤：必须是这里面的一档，不能凭感觉画线）：")
    for lvl in 候选levels[:3]:
        atr_pct = m.get("atr_d_pct")
        if not atr_pct:
            continue
        entry = round(lvl["mid"], 6)
        stop, R, target = calc_stop_target(entry, atr_pct)
        touched_recent = lvl.get("gap", 999) <= 15
        方向倍数 = 0.5
        仓位倍数 = 0.3333 if touched_recent else 1.0
        实际倍数 = 方向倍数 * 仓位倍数
        候选项 = {"进场": entry, "距现价%": lvl["dist"], "段数": lvl["segs"], "跨天": lvl["days"],
                "龄天": lvl["age"], "支": lvl["sup"], "压": lvl["res"], "最后触及": lvl.get("last"),
                "止损": stop, "R": R, "目标": target, "方向倍数": 方向倍数, "仓位倍数": 仓位倍数,
                "实际倍数": round(实际倍数,4), "15天内碰过": touched_recent}
        结果["候选"].append(候选项)
        if verbose:
            print(f"    · {entry:,.6g}（距现价{lvl['dist']:+.2f}%，"
                  f"{lvl['segs']}段/跨{lvl['days']}天/龄{lvl['age']}天/支{lvl['sup']}压{lvl['res']}，"
                  f"最后触及{lvl.get('last','?')}）")
            print(f"      若做空：止损{stop:,.6g}（+{(stop-entry)/entry*100:.2f}%）"
                  f"目标{target:,.6g}（{(target-entry)/entry*100:.2f}%）"
                  f"方向倍数0.5×仓位倍数{仓位倍数:.4f}=实际{实际倍数:.4f}"
                  f"{'（15天内碰过，打折）' if touched_recent else ''}")
    return 结果


def 扫全部(M, syms=None, verbose=True):
    """供 每日取数.py 直接调用：传入已经load好的指标dict，返回 {标的: 结果} ——
    不重新读文件、不重新print标题行，方便折进合并输出。"""
    syms = syms or list(M.keys())
    out = {}
    for sym in syms:
        if sym not in M:
            if verbose: print(f"{sym}：指标.json 里没有")
            continue
        out[sym] = 扫一个币(sym, M[sym], verbose=verbose)
    return out


def main():
    p = os.path.join(HERE, "指标.json")
    if not os.path.exists(p):
        sys.exit("缺 指标.json")
    M = json.load(open(p, encoding="utf-8"))
    syms = sys.argv[1:] or list(M.keys())
    print("做空候选筛选 —— 五道门，前两道硬性，第三道两条路（趋势已破 / 具体基本面利空），第四道只当佐证，第五道位置铁律")
    扫全部(M, syms, verbose=True)


if __name__ == "__main__":
    main()
