#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每天在你自己电脑上跑这一个命令，跑完把 每日汇总.json 发给 Claude（一个文件就够）。

用法（09-29 合并做空筛选和压力位回测之后，只剩这三种）
  python3 每日取数.py                        # 日常默认，只拉必须覆盖的币
  python3 每日取数.py PONS                   # 再加上今天想看的热点币
  python3 每日取数.py --周六                  # 周六复盘用，跑完提醒你连 raw.json 一起发
  python3 每日取数.py --价位 SOL 120,124.99   # 临时核实某个具体价位，偶尔用

一条命令跑七件事（09-20/09-21合并前四个脚本+两份输出，09-29再把做空筛选、
压力位回测这两块自动折进来，不用再单独跑、单独发）
  1. 拉数据.py          → 算指标用的K线原始数据
  2. 指标与关键位.py     → 出 指标.json
  3. 成交额排行榜.py     → 出 成交额排行.json（V5.1新增的"成交额排行榜法"选币路径，
                          见操作规格第06节：排除常驻名单、市值>=10亿的成交额靠前候选）
  4. 做空筛选.py         → 对本期覆盖的每个币跑一遍P0-P5做空五道门，结构化结果直接进汇总
  5. 压力位回测.py       → 对本期覆盖的每个币，把接触段位置拿去做历史正向回测
                          （胜率、平均回撤），跟④互补：④答"现在这档够不够格"，
                          ⑤答"这档历史上测试过的时候表现如何"
  6. L1L4日更.py        → （10-14新增，规格v6.5）下载网站 L1/L2/L3/L4 当日读数，算每个币的
                          费率30日均（写进指标.json的 fund_30d_ann，让做空筛选的30日均<0否决生效），
                          成交额排行按 5 亿市值门槛重建，出送审稿四行（存 送审稿四行.txt）
  7. 合并               → 把上面几份合成 每日汇总.json，你只用发这一个文件

币怎么选
  固定 2 个     BTC、SOL
  优先池        BNB、UNI、CAKE、HYPE（09-20新增：回购销毁优先池，机制已核实为链上
                真实执行，见操作规格V5.0第06节，每天自动带上，不用手动敲）
  山寨轮动观察池 ENA、TAO、RAY、DOGE、LDO、ARB、NEAR、ZEC、JUP（09-21新增：
                《山寨补涨板块轮动预警框架》V1 + 《BTC节奏跟随》两份文档里的
                补涨候选优先级1/2名单，每天自动带上，用来喂"山寨轮动纸面观察日志"，
                纯观察验证，不进P0-P3实盘判断）
  必须覆盖      orders.json 里所有「持仓中」和「挂单中」的币
                （铁律：有未平仓或未成交挂单的币，本期必须覆盖，不管今天热不热）
  你加的        命令行后面跟的，今天的热点币
  ETH           默认带上（宏观面板要用）；BNB 现在归优先池自动带上，不再单独处理

只用标准库，不需要 pip install 任何东西。
拉完会自检：缺数据、跨度不足 250 天（禁用慢档）都会当场告诉你。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import 做空筛选
import 压力位回测
try:
    import L1L4日更   # 10-14新增：L1–L4读数 + 费率30日均 + 5亿门槛成交额榜
except ImportError:
    L1L4日更 = None
固定 = ["BTC", "SOL", "ETH", "XRP"]  # 09-29新增XRP：五大主流币(BTC/ETH/SOL/XRP/BNB)里
# 前三个在固定、BNB在优先池，只有XRP两边都不在，之前靠orders.json硬覆盖，那条路废了就直接补进固定
优先池 = ["BNB", "UNI", "CAKE", "HYPE"]  # 09-20新增：回购销毁优先池，每天自动覆盖
山寨轮动观察池 = ["ENA", "TAO", "RAY", "DOGE", "LDO", "ARB", "NEAR", "ZEC", "JUP"]
# 09-21新增：喂"山寨轮动纸面观察日志"用的，纯观察验证，见上方说明


def 必须覆盖():
    """orders.json 已经跟Notion台账脱节废弃了（09-29确认），不再维护任何同步文件——
    五大主流币全部已经在 固定+优先池 里，不需要这条规则再单独补漏。
    万一以后真开仓到一个完全没被覆盖的新币种，Claude写Notion台账的时候会当场提醒
    "这个币不在覆盖名单里，要不要加进固定/优先池"，不需要你平时维护任何东西。"""
    return []


def run(script, args=(), 严格=True):
    p = os.path.join(HERE, script)
    if not os.path.exists(p):
        sys.exit(f"找不到 {script}，请确认它和本脚本在同一个文件夹")
    print(f"\n─── {script} ───")
    r = subprocess.run([sys.executable, p, *args], cwd=HERE)
    if r.returncode != 0:
        msg = f"{script} 跑失败了（退出码 {r.returncode}）。"
        if 严格:
            sys.exit(f"\n{msg}如果是网络错误，确认这台机器能连 data-api.binance.vision 和 www.binance.com")
        else:
            print(f"\n  ⚠️ {msg}不影响 指标.json，跳过这步继续。"
                  "常见原因是CoinGecko限速，晚点重跑 python3 成交额排行榜.py 单独补一次就行。")
    return r.returncode == 0


def main():
    argv = sys.argv[1:]
    周六 = "--周六" in argv
    manual_symbol, manual_levels = None, None
    if "--价位" in argv:
        # 约定顺序：--价位 SYMBOL 数字,数字  （标的在前，价位在后，各占一个参数）
        idx = argv.index("--价位")
        manual_symbol = argv[idx+1].upper()
        manual_levels = [float(x) for x in argv[idx+2].split(",")]
        # 从参数列表里摘掉这三格，不然 SYMBOL 会被当成"今天加的热点币"重复处理
        argv = argv[:idx] + argv[idx+3:]
    额外 = [a.upper() for a in argv if not a.startswith("--")]

    live = 必须覆盖()
    coins = list(dict.fromkeys(固定 + 优先池 + 山寨轮动观察池 + live + 额外))

    print("=" * 66)
    print("本期取数的币")
    print(f"  固定      {'、'.join(固定)}")
    print(f"  优先池    {'、'.join(优先池)}   ← 回购销毁优先池，每天自动带上")
    print(f"  轮动观察池 {'、'.join(山寨轮动观察池)}   ← 山寨轮动纸面观察用，每天自动带上")
    print(f"  有活单    {'、'.join(live) if live else '（无）'}   ← 铁律：必须覆盖")
    print(f"  今天加的  {'、'.join(额外) if 额外 else '（无）'}")
    print(f"  合计 {len(coins)} 个：{'、'.join(coins)}")
    print("=" * 66)

    if manual_symbol and manual_symbol not in coins:
        coins.append(manual_symbol)
        print(f"  临时价位核实：{manual_symbol} 不在今天原本要覆盖的名单里，已自动加进本期取数")

    run("拉数据.py", coins)
    run("指标与关键位.py")
    成交额排行_成功 = run("成交额排行榜.py", 严格=False)  # 09-20新增，见操作规格V5.1第06节

    # 自检
    p = os.path.join(HERE, "指标.json")
    if not os.path.exists(p):
        sys.exit("指标.json 没生成，上一步应该报错了")
    d = json.load(open(p, encoding="utf-8"))
    print("\n" + "=" * 66)
    print("自检")
    缺 = [c for c in coins if c not in d]
    if 缺:
        print(f"  ⚠️ 这几个币没拉到，币安可能没有它的永续合约：{'、'.join(缺)}")
        print("     按规则，币安没有永续合约的币不进方向总表，只能在正文里当故事讲")
    有警 = False
    for c, r in d.items():
        警 = []
        if not r.get("慢档可用"):
            警.append(f"日线跨度只有 {r.get('span_days')} 天（<250），禁用慢档，只能走快档 4h")
        不足 = r.get("数据不足") or []
        if 不足:
            警.append(f"日线只有 {r.get('bars_d')} 根，算不出：{'、'.join(不足)}")
        if 警:
            有警 = True
            print(f"  ⚠️ {c}")
            for w in 警:
                print(f"       {w}")
    if not 缺 and not 有警:
        print("  全部正常")
    elif 有警:
        print("\n  上面这些不是报错。新上市的币日线本来就短，"
              "按规则它们只能走快档 4h，或者干脆只在正文里当故事讲。")

    kb = os.path.getsize(p) / 1024
    print("\n" + "=" * 66)

    # 09-21新增：把 指标.json 和 成交额排行.json 合并成一个文件，只用发一份
    汇总 = {"指标": d}
    cj = os.path.join(HERE, "成交额排行.json")
    if 成交额排行_成功 and os.path.exists(cj):
        汇总["成交额排行"] = json.load(open(cj, encoding="utf-8"))
    else:
        汇总["成交额排行"] = None
        print("  ⚠️ 成交额排行.json 这次没生成，合并文件里这部分会是 null，不影响指标那部分")

    # 10-14新增（规格v6.5）：L1–L4当日读数 + 每币费率30日均 + 成交额排行按5亿门槛重建。
    # 必须放在做空筛选之前：这一步会原地往 d 里补 fund_30d_ann，做空筛选的30日均<0否决才有数据。
    # 整步包在 try 里，网站或币安接口出问题只会记进缺口，不会让日常取数中断。
    print("\n" + "=" * 66)
    print("跑 L1–L4 读数 + 费率30日均 + 成交额榜（5亿门槛）...")
    l1l4文本 = None
    if L1L4日更 is None:
        print("  ⚠️ 没找到 L1L4日更.py（要和本脚本放同一个文件夹），这一步跳过：做空筛选的30日均否决不会生效")
    else:
        try:
            r = L1L4日更.collect(HERE, d, old_rank=汇总.get("成交额排行"))
            汇总["L1L4"] = r["L1L4"]
            if r["成交额排行"]:
                汇总["成交额排行"] = r["成交额排行"]
                print(f"  成交额榜已按 {r['成交额排行']['市值门槛_美元']/1e8:.0f} 亿门槛重建，候选 {len(r['成交额排行']['候选'])} 个")
                L1L4日更.print_rank_diff(r["成交额排行"])
            l1l4文本 = r["文本"]
        except Exception as e:
            print(f"  ⚠️ L1L4日更 这步失败了（{e}），不影响其他部分；做空筛选的30日均否决这次不生效")

    # 09-29新增：做空筛选五道门，对本期覆盖的每个币都跑一遍，结构化结果直接进汇总，
    # 不用再单独跑 做空筛选.py、单独发结果
    print("\n" + "=" * 66)
    print("跑做空筛选（五道门）...")
    汇总["做空筛选"] = 做空筛选.扫全部(d, syms=coins, verbose=False)
    有候选 = [s for s, r in 汇总["做空筛选"].items() if r.get("候选")]
    print(f"  {len(有候选)}/{len(coins)} 个币过了门①②、有候选压力位：{'、'.join(有候选) if 有候选 else '（无）'}")

    # 09-29新增：压力位历史胜率回测，同样对本期覆盖的每个币都跑，
    # 这一步要逐个币拉全部历史K线，比前面几步慢，正常
    print("\n" + "=" * 66)
    print(f"跑压力位历史回测（{len(coins)}个币，要逐个拉全部历史K线，稍等）...")
    汇总["压力位回测"] = 压力位回测.run(coins, manual_symbol, manual_levels, verbose=True)

    out = os.path.join(HERE, "每日汇总.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(汇总, f, ensure_ascii=False, indent=2)

    print(f"跑完了。把这一个文件发给 Claude：\n  {out}   （{os.path.getsize(out)/1024:.0f} KB）")
    if 周六:
        raw = os.path.join(HERE, "raw.json")
        mb = os.path.getsize(raw) / 1048576 if os.path.exists(raw) else 0
        print(f"\n周六复盘还要这个（逐单重算 K 线用，这个不合并，单独发）：\n  {raw}   （{mb:.1f} MB）")
    if l1l4文本:
        print("\n" + "=" * 66)
        print("送审稿 L1–L4 四行（已存 送审稿四行.txt，也在 每日汇总.json 的 L1L4 里）：")
        print(l1l4文本)
    print("\n连同今天的 Grok 热点原文一起发过来就行。")

if __name__ == "__main__":
    main()
