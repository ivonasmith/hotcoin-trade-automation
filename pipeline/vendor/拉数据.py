#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热币分析 · 行情数据拉取（v2.1）

用法
    python3 拉数据.py BTC SOL ETH XRP 牛来
    （一般由 每日取数.py 调用，不用直接跑）

输出
    与本脚本同目录的 raw.json

════════ v2.1 改了什么，为什么 ════════

1. urllib → requests，并且【显式指定代理】。
   FlClash 的 TUN 模式会把 binance.com 按分流规则判成直连，
   于是 urllib 裸连币安、被拦截、拿到假证书，
   报成 "self-signed certificate in certificate chain"。
   显式走 7890 就没这个问题——这是你 fetch_meme_data.py 里验证过的做法。
   顺带 requests 自带 certifi 证书库，绕开了 macOS 上 Python 根证书那一堆事。

2. 加了主备域名自动切换。
   fapi.binance.com / api.binance.com 在部分地区被墙（表现为连接超时），
   但 www.binance.com 下的同名路径是通的。

3. 加了会话级指数退避重试，应付代理链路时好时坏
   （SSLEOFError / ConnectionResetError 这类"连上了但中途断开"）。

4. 支持非 ASCII 交易对。币安给中文名 MEME 币用的就是中文 symbol，
   比如「牛来」的永续合约是 牛来USDT，不是 NIULAIUSDT
   （NIULAI 是 KuCoin/BingX/CMC 的写法，币安不用）。
   ⚠️ 传原始字符串就行，绝对不要自己先 URL 编码——
      requests 会再编码一次，% 变成 %25，symbol 就废了。

依赖：requests（你机器上已经有了）
"""

import json
import os
import sys
import time

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    sys.exit("缺 requests：pip3 install requests")

# ════════════════════════════════════════════════════════
# CONFIG —— 要改的都在这里
# ════════════════════════════════════════════════════════

# 网络代理。换节点/换端口改这里；不需要代理就改成 PROXIES = None
_cloud_proxy = os.environ.get("HOTCOIN_HTTP_PROXY")
PROXIES = {"http": _cloud_proxy, "https": _cloud_proxy} if _cloud_proxy else None
# PROXIES = None

D1_BARS = 2000      # 日线根数（慢档找位要 1600 根，留点余量）
H4_BARS = 500       # 4 小时根数

# ════════════════════════════════════════════════════════

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "raw.json")
UA = {"User-Agent": "Mozilla/5.0"}

# 域名铁律：合约走 www.binance.com/fapi，主备都在这里
FAPI = "https://fapi.binance.com"
FAPI_FALLBACK = "https://www.binance.com"
# ❌ 别试 api.binance.com、api1~4、Bybit —— 地域封锁

_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=5, connect=5, read=5, backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])))
_session.mount("http://", _session.adapters["https://"])


def get(path, params=None, retries=2, timeout=25):
    """带主备域名切换的 GET。path 形如 /fapi/v1/klines"""
    for idx, base in enumerate((FAPI, FAPI_FALLBACK)):
        url = base + path
        if idx > 0:
            print(f"    → 主域名不通，切备用: {base}")
        for i in range(retries):
            try:
                r = _session.get(url, params=params, headers=UA,
                                 timeout=timeout, proxies=PROXIES)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                name = type(e).__name__
                if "ConnectTimeout" in name or "timed out" in str(e):
                    print(f"    [超时] {base} 连不上（可能被墙）")
                    break
                if "SSL" in name or "CERTIFICATE" in str(e).upper():
                    print(f"    [证书错误] {e}")
                    print("      → 多半是没走代理。检查脚本顶部 PROXIES，"
                          "或确认 FlClash 在跑、端口是 7890")
                    break
                print(f"    [警告] ({i+1}/{retries}) {name}: {e}")
                time.sleep(2)
    print(f"    [失败] 放弃 {path} {params}")
    return None


def klines(symbol, interval, want):
    """拉 K 线。limit 上限 1000，往前翻用 endTime=第一根ts-1"""
    k = get("/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": min(want, 1000)})
    if not k:
        return None
    while len(k) < want:
        time.sleep(0.3)
        more = get("/fapi/v1/klines",
                   {"symbol": symbol, "interval": interval, "limit": 1000,
                    "endTime": int(k[0][0]) - 1})
        if not more:
            break
        k = more + k
        if len(more) < 1000:
            break          # 到头了，这个币就这么长的历史
    return k


def pull(coins):
    out, 失败 = {}, []
    for c in coins:
        # ⚠️ 原始字符串，不要 URL 编码。中文 symbol 币安是认的（牛来USDT）
        s = c + "USDT"
        print(f"\n  {c}  ({s})")
        d = {}
        d["d1"] = klines(s, "1d", D1_BARS)
        if not d["d1"]:
            print(f"    ✗ 拿不到 {s} 的日线——币安可能没有这个永续合约")
            失败.append(c)
            continue
        time.sleep(0.3)
        d["h4"] = klines(s, "4h", H4_BARS)
        time.sleep(0.3)
        d["prem"] = get("/fapi/v1/premiumIndex", {"symbol": s})
        time.sleep(0.3)
        d["oi"] = get("/fapi/v1/openInterest", {"symbol": s})
        if d["oi"] is None:
            # 09-22新增：活跃的永续合约不可能没有OI数据，OI拿不到基本等于这个合约
            # 已经被币安摘牌/停止交易了（RAYUSDT就是这样，2022-11-15就被摘牌了，
            # 但K线接口还在吐旧的/冻结的残留数据，导致价格看起来像"一动不动"）。
            # 这种情况不要用已经拿到的K线，直接当失败处理。
            print(f"    ✗ {s} 拿不到OI数据——大概率这个合约已经被币安摘牌，"
                  f"K线就算能拉到也是失效数据，不采用")
            失败.append(c)
            continue
        time.sleep(0.3)
        d["ls"] = get("/futures/data/globalLongShortAccountRatio",
                      {"symbol": s, "period": "1h", "limit": 1})
        time.sleep(0.3)
        out[c] = d
        print(f"    ✓ 日线 {len(d['d1'])} 根   4h {len(d['h4'] or [])} 根")
    return out, 失败


def main():
    coins = sys.argv[1:] or ["BTC", "SOL", "ETH"]
    print(f"取数 {len(coins)} 个币：{'、'.join(coins)}")
    print(f"代理：{'已配置代理' if PROXIES else '不走代理'}")

    out, 失败 = pull(coins)
    if not out:
        sys.exit("\n一个币都没拉到。先确认 FlClash 在跑、端口 7890、"
                 "并且脚本顶部 PROXIES 没被改成 None")

    json.dump(out, open(OUT, "w"), ensure_ascii=False)
    mb = os.path.getsize(OUT) / 1048576
    print(f"\n已写入 {OUT}  （{mb:.1f} MB，{len(out)} 个币）")
    if 失败:
        print(f"没拉到：{'、'.join(失败)}")
        print("  币安没有永续合约的币，按规则不进方向总表，只在正文里当故事讲")


if __name__ == "__main__":
    main()
