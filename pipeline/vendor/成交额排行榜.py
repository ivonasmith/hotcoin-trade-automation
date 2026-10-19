#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日成交额排行榜法 · 选币发现脚本

对应操作规格V5.1第06节"成交额排行榜法"：
  1. 拉币安USDT本位永续合约的24小时成交额，按成交额从高到低排
  2. 排除常驻名单（本来就天天在看，不需要这条路径重复发现）
  3. 用CoinGecko查市值，只留 >= 10亿美元的
  4. 输出剩下的候选，按成交额排名

只用标准库（urllib），不需要 pip install。
跟每日取数.py是两个独立脚本，各自跑各自的，不互相调用——
这条选币路径产出的是"候选名单"，不直接进指标.json，
选中之后该走06节两道闸、08节P0-P3的还是要走，脚本不代替判断。

用法
  python3 成交额排行榜.py          # 默认输出前20名候选
  python3 成交额排行榜.py 30       # 想看前30名候选
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))

# 常驻名单：BTC/ETH/SOL/XRP/BNB（长期覆盖）+ UNI/CAKE/HYPE（回购销毁优先池）
# 见操作规格V5.1第06节。这八个不需要靠这条路径"发现"，本来就天天在看。
常驻名单 = {"BTC", "ETH", "SOL", "XRP", "BNB", "UNI", "CAKE", "HYPE"}

市值门槛_美元 = 10 * 100_000_000  # 10亿美元，跟"轮换3个"共用同一条线（V5.1）

FAPI = "https://fapi.binance.com"
COINGECKO = "https://api.coingecko.com/api/v3"


def 请求(url, 超时=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=超时) as r:
        return json.loads(r.read().decode("utf-8"))


def 拉永续合约列表():
    """币安USDT本位永续合约，状态TRADING的，只要symbol"""
    d = 请求(f"{FAPI}/fapi/v1/exchangeInfo")
    出 = set()
    for s in d["symbols"]:
        if (s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"):
            出.add(s["baseAsset"].upper())
    return 出


def 拉24小时成交额():
    """{symbol: {quoteVolume, lastPrice}}，symbol是USDT本位永续的ticker，比如BTCUSDT"""
    d = 请求(f"{FAPI}/fapi/v1/ticker/24hr")
    出 = {}
    for x in d:
        if not x["symbol"].endswith("USDT"):
            continue
        base = x["symbol"][:-4].upper()
        出[base] = {
            "quoteVolume": float(x["quoteVolume"]),
            "lastPrice": float(x["lastPrice"]),
        }
    return 出


def 拉市值表(页数=2, 每页=250):
    """CoinGecko市值前 页数×每页 名，{SYMBOL: market_cap}
    同一个symbol如果有多个币种共用（比如不同链上的同名代币），
    market_cap更大的那个会后写入，覆盖前面的——市值排序是从高到低拉的，
    所以实际生效的是"用这个ticker的币里市值最大的那个"，符合常识预期。
    """
    出 = {}
    for page in range(页数, 0, -1):  # 从后往前写，让市值最大的最后写入、生效
        url = (f"{COINGECKO}/coins/markets?vs_currency=usd&order=market_cap_desc"
               f"&per_page={每页}&page={page}&sparkline=false")
        try:
            d = 请求(url)
        except urllib.error.HTTPError as e:
            print(f"  ⚠️ CoinGecko第{page}页拉取失败（{e}），可能是限速，跳过这页")
            continue
        for c in d:
            sym = c["symbol"].upper()
            mc = c.get("market_cap") or 0
            出[sym] = mc
        time.sleep(1.5)  # CoinGecko免费额度有速率限制，慢一点别被拒
    return 出


def main():
    前N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 20

    print("=" * 66)
    print("成交额排行榜法 · 选币发现")
    print("=" * 66)

    print("\n① 拉币安永续合约列表 …")
    永续 = 拉永续合约列表()
    print(f"   共 {len(永续)} 个USDT本位永续合约")

    print("\n② 拉24小时成交额 …")
    成交额 = 拉24小时成交额()

    print("\n③ 拉CoinGecko市值表（前500名，分2页，注意限速会稍慢）…")
    市值表 = 拉市值表()
    print(f"   拿到 {len(市值表)} 个币种的市值数据")

    print("\n④ 按成交额排序、过滤 …")
    候选 = []
    for base, v in 成交额.items():
        if base not in 永续:
            continue  # 理论上不会发生，成交额数据本身就来自永续列表，双重保险
        if base in 常驻名单:
            continue
        mc = 市值表.get(base)
        if mc is None or mc < 市值门槛_美元:
            continue
        候选.append({
            "标的": base,
            "24h成交额_美元": round(v["quoteVolume"]),
            "现价": v["lastPrice"],
            "市值_美元": round(mc),
        })

    候选.sort(key=lambda x: x["24h成交额_美元"], reverse=True)
    候选 = 候选[:前N]

    print("\n" + "=" * 66)
    print(f"候选名单（前{前N}，已排除常驻名单、市值<10亿的）")
    print("=" * 66)
    for i, c in enumerate(候选, 1):
        print(f"  {i:>2}. {c['标的']:<8} 成交额 ${c['24h成交额_美元']:,}"
              f"   市值 ${c['市值_美元']:,}   现价 {c['现价']}")

    out_path = os.path.join(HERE, "成交额排行.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "生成时间_UTC": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "市值门槛_美元": 市值门槛_美元,
            "常驻名单": sorted(常驻名单),
            "候选": 候选,
        }, f, ensure_ascii=False, indent=2)

    kb = os.path.getsize(out_path) / 1024
    print("\n" + "=" * 66)
    print(f"存好了：{out_path}   （{kb:.0f} KB）")
    print("这份只是候选名单，选中之后该走06节两道闸、08节P0-P3的还是要走。")
    print("连同今天的 指标.json 一起发给 Claude 就行。")


if __name__ == "__main__":
    main()
