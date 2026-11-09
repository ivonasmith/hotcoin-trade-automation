"""市场异动榜：程序自己算出"今天哪里在动"，不依赖人工贴热点。

只用币安公开接口 + CoinGecko 热搜 + 币安公告，全部免费。输出的是事实和数字，
不解释原因（原因要靠新闻/叙事，程序读不到就不编）。任何一路取不到，都会记进"缺口"，
不会拿旧数据顶替，也不会让每日取数失败。
"""
import os
import time

import requests

FAPI = "https://fapi.binance.com"
FAPI_FALLBACK = "https://www.binance.com"
COINGECKO = "https://api.coingecko.com/api/v3"
ANNOUNCE = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
            "?type=1&catalogId=48&pageNo=1&pageSize=15")

# 口径（改这里即可）
UP_PCT = 8.0          # 24h 涨幅达到即标"大涨"
DOWN_PCT = -8.0       # 24h 跌幅达到即标"大跌"
VOL_MULT = 2.0        # 24h 成交额 ≥ 前7日日均的这个倍数，标"放量"
FUND_PCT = 0.05       # |单期资金费率| ≥ 此值（%），标"费率偏正/偏负"
TOP_N = 8             # 异动榜最多列几个
KLINE_COINS = 45      # 最多逐个拉日线的币数（按24h成交额从高到低）
ANNOUNCE_HOURS = 48
KEYWORDS = ("上线", "上市", "下架", "Will List", "Will Add", "Delist", "Launchpool", "HODLer")

_proxy = os.environ.get("HOTCOIN_HTTP_PROXY")
PROXIES = {"http": _proxy, "https": _proxy} if _proxy else None


def http_json(url, params=None, timeout=20):
    last = None
    for _ in range(2):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"},
                             timeout=timeout, proxies=PROXIES)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001 - reported as a gap, never raised
            last = exc
            time.sleep(1)
    raise last


def fapi(path, params=None, fetch=http_json):
    try:
        return fetch(FAPI + path, params)
    except Exception:  # noqa: BLE001
        return fetch(FAPI_FALLBACK + path, params)


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def tags_for(change, mult, funding):
    tags = []
    if change is not None and change >= UP_PCT:
        tags.append("大涨")
    if change is not None and change <= DOWN_PCT:
        tags.append("大跌")
    if mult is not None and mult >= VOL_MULT:
        tags.append("放量")
    if funding is not None and abs(funding) >= FUND_PCT:
        tags.append("费率偏正" if funding > 0 else "费率偏负")
    return tags


def rank_movers(rows, top_n=TOP_N):
    """rows: [{标的, 涨跌24h_pct, 成交额_美元, 成交额倍数, 资金费率_pct}] → 带标签并排序后的前N。"""
    out = []
    for row in rows:
        tags = tags_for(row.get("涨跌24h_pct"), row.get("成交额倍数"), row.get("资金费率_pct"))
        if not tags:
            continue
        score = abs(row.get("涨跌24h_pct") or 0) / 4 + max((row.get("成交额倍数") or 1) - 1, 0) \
            + (1 if any(t.startswith("费率") for t in tags) else 0)
        out.append({**row, "标签": tags, "_score": score})
    out.sort(key=lambda r: r["_score"], reverse=True)
    for row in out:
        row.pop("_score")
    return out[:top_n]


def scan(summary, fetch=http_json, now=None):
    """summary: 每日汇总（取其中 指标 的币和 成交额排行 候选作为币池）。"""
    now = now or time.time()
    gaps = []
    universe = set((summary.get("指标") or {}).keys())
    rank = summary.get("成交额排行") or {}
    universe |= {c["标的"] for c in rank.get("候选") or [] if c.get("标的")}

    movers, stats = [], {}
    try:
        for t in fapi("/fapi/v1/ticker/24hr", fetch=fetch):
            sym = str(t.get("symbol", ""))
            if sym.endswith("USDT") and sym[:-4] in universe:
                stats[sym[:-4]] = {"chg": _num(t.get("priceChangePercent")),
                                   "vol": _num(t.get("quoteVolume")), "px": _num(t.get("lastPrice"))}
    except Exception as exc:  # noqa: BLE001
        gaps.append("24小时行情取不到：" + type(exc).__name__)

    funding = {}
    try:
        for item in fapi("/fapi/v1/premiumIndex", fetch=fetch):
            sym = str(item.get("symbol", ""))
            rate = _num(item.get("lastFundingRate"))
            if sym.endswith("USDT") and rate is not None:
                funding[sym[:-4]] = rate * 100
    except Exception as exc:  # noqa: BLE001
        gaps.append("资金费率取不到：" + type(exc).__name__)

    by_volume = sorted(stats, key=lambda s: stats[s]["vol"] or 0, reverse=True)[:KLINE_COINS]
    mult_missing = 0
    for sym in by_volume:
        mult = None
        try:
            bars = fapi("/fapi/v1/klines", {"symbol": sym + "USDT", "interval": "1d", "limit": 9}, fetch=fetch)
            closed = [_num(b[7]) for b in bars[:-1]][-7:]  # 最后一根是未收盘的今天
            closed = [v for v in closed if v]
            if len(closed) >= 5 and stats[sym]["vol"]:
                mult = round(stats[sym]["vol"] / (sum(closed) / len(closed)), 2)
        except Exception:  # noqa: BLE001
            mult_missing += 1
        chg = stats[sym]["chg"]
        movers.append({"标的": sym, "现价": stats[sym]["px"],
                       "涨跌24h_pct": round(chg, 2) if chg is not None else None,
                       "成交额_美元": round(stats[sym]["vol"] or 0), "成交额倍数": mult,
                       "资金费率_pct": round(funding[sym], 4) if sym in funding else None})
    if mult_missing:
        gaps.append(f"{mult_missing}个币的放量倍数没算出（日线取不到）")

    trending = []
    try:
        data = fetch(COINGECKO + "/search/trending", None)
        trending = [{"币": c["item"].get("symbol", "").upper(), "市值排名": c["item"].get("market_cap_rank")}
                    for c in data.get("coins", [])[:7] if c.get("item")]
    except Exception as exc:  # noqa: BLE001
        gaps.append("CoinGecko热搜取不到：" + type(exc).__name__)

    notices = []
    try:
        data = fetch(ANNOUNCE, None)
        articles = ((data.get("data") or {}).get("catalogs") or [{}])[0].get("articles") or []
        for art in articles:
            title, released = str(art.get("title", "")), _num(art.get("releaseDate"))
            if released and now * 1000 - released <= ANNOUNCE_HOURS * 3600 * 1000 \
                    and any(k in title for k in KEYWORDS):
                notices.append(title[:90])
    except Exception as exc:  # noqa: BLE001
        gaps.append("币安公告取不到：" + type(exc).__name__)

    return {
        "口径": f"币池=每日取数的币+成交额榜候选；大涨≥{UP_PCT}%/大跌≤{DOWN_PCT}%/放量≥前7日日均{VOL_MULT}倍/"
                f"费率|单期|≥{FUND_PCT}%；只陈述数字，不解释原因",
        "生成时间_UTC": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
        "币池数": len(stats),
        "异动": rank_movers(movers),
        "币安公告48h": notices[:6],
        "CoinGecko热搜": trending,
        "缺口": gaps,
    }
