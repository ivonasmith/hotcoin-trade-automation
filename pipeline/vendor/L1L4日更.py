#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1L4日更.py —— 每日取数的 L1–L4 一并抓取模块（规格 v6.5，2026-10-14）

放在每日取数脚本同一个文件夹，跟 指标.json / 每日汇总.json / 做空筛选.py 放一起。
两种用法，二选一：
  ① 已并进 每日取数.py（调用 L1L4日更.collect），日常什么都不用多敲
  ② 想单独补跑：  python3 L1L4日更.py            （默认就是脚本所在文件夹，直接补丁 指标.json / 每日汇总.json）
                 python3 L1L4日更.py /path/to/运行目录

它做五件事（全部自动存进运行目录，不用手动保存）：
  1. 从网站公开数据（GitHub raw）下载 L1/L2/L3/L4 当日读数：
       l2_latest.json、l3_latest.json、site_latest.json（= site/data/latest.json，含 L1 全部周读数和 L4 的恐慌贪婪 / HL 费率 / OI）
  2. 算 L4 唯一改动作的那一项：费率 30 日均（年化）——BTC 和 指标.json 里每一个币，
       口径：币安合约 fundingRate，按 UTC 日加总，取最近 30 个完整日的均值 ×365 ×100（和 L4 研究一致）；
       币安取不到的币用 Hyperliquid 兜底，并在 fund_30d_src 里标出来（HL 口径含约 11% 基准利息，偏高，只作兜底）。
     写进 指标.json 的 fund_30d_ann / fund_7d_ann / fund_30d_src。
  3. 用 做空筛选.py（v6.4）重跑一遍，把 每日汇总.json 里过期的「做空筛选」块刷新（30 日均 <0 的否决由此生效）。
  4. 成交额排行按新门槛重建：市值门槛 500,000,000（v6.5，5 亿），写回 每日汇总.json 的「成交额排行」。
  5. 出送审稿四行（宏观 L1 / 周期 L2 / 筹码 L3 / 衍生品 L4）和两个折扣条件的判定，存成 送审稿四行.txt + L1L4读数.json。

只用 Python 标准库。任何一步失败都不会中断其他步骤，最后统一打印「缺口」清单。
"""
import json
import os
import sys
import time
import importlib.util
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

# ============ 参数（要改只改这里） ============
MCAP_FLOOR = 500_000_000          # v6.5：选币市值门槛 5 亿美元（v6.4 及以前是 10 亿）
TOP_N = 20                         # 成交额榜取前几名候选（旧榜是 20 个）
RESIDENT = ["BNB", "BTC", "CAKE", "ETH", "HYPE", "SOL", "UNI", "XRP"]   # 常驻名单，成交额榜法排除
STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USDS", "PYUSD", "BUSD", "USD1", "RLUSD",
           "USDD", "EURI", "AEUR", "EUR", "USTC", "XUSD", "BFUSD", "USDG"}
GH_RAW = "https://raw.githubusercontent.com/ivonasmith/uncleonchain/main/"
SITE_FILES = {                       # 下载到运行目录的名字 ← 仓库路径
    "l2_latest.json": "data/l2/l2_latest.json",
    "l3_latest.json": "data/l3/l3_latest.json",
    "site_latest.json": "site/data/latest.json",
}
FAPI_BASES = ["https://fapi.binance.com", "https://www.binance.com"]    # 合约接口，依次尝试
HL_URL = "https://api.hyperliquid.xyz/info"
CG_URL = "https://api.coingecko.com/api/v3/coins/markets"
UA = {"User-Agent": "L1L4-daily/1.0"}

# 规则阈值（全部来自规格 v6.5 第 08 节，写死在这里方便对账）
NETLIQ_TH = -2.72      # 净流动性 13 周 ≤ -2.72% 且美元 13 周走强 → ×0.7
REAL_TH = 0.40         # 实际利率 13 周 ≥ +0.40pp 且 (BAA 走阔 / NFCI 转紧 / 乐观度 z<0) → ×0.7
VOL_TH, CONC_TH = 40.0, 12.0   # 大波动预警：30 天波动 <40% 且 ±5% 筹码集中度 ≥12%


# ============ 网络层（测试时可整体替换） ============
def _get(url, timeout=25, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:      # noqa
            last = e
            time.sleep((6 if "429" in str(e) else 1.2) * (i + 1))
    raise RuntimeError(f"GET 失败 {url[:90]}: {last}")


def _post(url, body, timeout=25, retries=2):
    last = None
    data = json.dumps(body).encode()
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:      # noqa
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"POST 失败 {url[:90]}: {last}")


def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _save(folder, name, obj):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _load(folder, name):
    p = os.path.join(folder, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ============ 1. 网站 L1–L4 当日读数 ============
def fetch_site(folder, gaps):
    out = {}
    for name, path in SITE_FILES.items():
        try:
            out[name] = _get(GH_RAW + path)
            _save(folder, name, out[name])
        except Exception as e:      # noqa
            old = _load(folder, name)
            out[name] = old
            gaps.append(f"网站数据 {name} 下载失败（{e}）" + ("，沿用运行目录里的旧文件" if old else "，且没有旧文件"))
    return out


# ============ 2. 费率 30 日均 ============
def binance_daily_funding(sym, days=40):
    """币安合约资金费率，按 UTC 日加总。sym 例：'ETH'。返回 {date: 当日费率之和}"""
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    last = None
    for base in FAPI_BASES:
        try:
            rows = _get(f"{base}/fapi/v1/fundingRate?symbol={sym}USDT&startTime={start}&limit=1000")
            if not isinstance(rows, list) or not rows:
                raise RuntimeError("空返回")
            daily = {}
            for r in rows:
                d = datetime.fromtimestamp(int(r["fundingTime"]) / 1000, timezone.utc).strftime("%Y-%m-%d")
                daily[d] = daily.get(d, 0.0) + float(r["fundingRate"])
            return daily
        except Exception as e:      # noqa
            last = e
    raise RuntimeError(f"币安 {sym}USDT 费率取不到: {last}")


def hl_daily_funding(sym, days=40):
    """Hyperliquid 逐小时费率，按 UTC 日加总（兜底口径）。"""
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    rows = _post(HL_URL, {"type": "fundingHistory", "coin": sym, "startTime": start})
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"HL {sym} 无费率")
    daily = {}
    for r in rows:
        d = datetime.fromtimestamp(int(r["time"]) / 1000, timezone.utc).strftime("%Y-%m-%d")
        daily[d] = daily.get(d, 0.0) + float(r["fundingRate"])
    return daily


def annualize(daily, n, min_days):
    """最近 n 个完整 UTC 日（不含今天）的日加总均值 ×365 ×100；样本不足返回 None。"""
    today = _utc_today()
    ds = sorted(d for d in daily if d < today)[-n:]
    if len(ds) < min_days:
        return None
    return round(sum(daily[d] for d in ds) / len(ds) * 365 * 100, 2)


def funding_one(sym):
    try:
        daily, src = binance_daily_funding(sym), "Binance"
    except Exception as e1:     # noqa
        try:
            daily, src = hl_daily_funding(sym), "HL"
        except Exception as e2:     # noqa
            return sym, {"fund_30d_ann": None, "fund_7d_ann": None, "fund_30d_src": None,
                         "err": f"{e1} | {e2}"}
    return sym, {"fund_30d_ann": annualize(daily, 30, 25), "fund_7d_ann": annualize(daily, 7, 6),
                 "fund_30d_src": src, "err": None}


def fetch_funding(folder, M, gaps):
    syms = list(dict.fromkeys(["BTC"] + (list(M.keys()) if M else [])))
    with ThreadPoolExecutor(max_workers=5) as ex:
        res = dict(ex.map(funding_one, syms))
    for s, r in res.items():
        if r["fund_30d_ann"] is None:
            gaps.append(f"{s} 费率30日均取不到" + (f"（{r['err']}）" if r["err"] else "（样本不足 25 天）"))
        elif r["fund_30d_src"] == "HL":
            gaps.append(f"{s} 费率30日均用的是 Hyperliquid 兜底口径（含约 11% 基准利息，比币安口径偏高，30日均<0 的否决会偏松）")
    _save(folder, "费率30日均.json", {"生成时间_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                                      "口径": "按 UTC 日加总费率，最近 30 个完整日均值 ×365 ×100", "币种": res})
    if M:
        for s in M:
            r = res.get(s)
            if r:
                M[s]["fund_30d_ann"] = r["fund_30d_ann"]
                M[s]["fund_7d_ann"] = r["fund_7d_ann"]
                M[s]["fund_30d_src"] = r["fund_30d_src"]
        _save(folder, "指标.json", M)
    return res


# ============ 3. 做空筛选（v6.4）重跑 ============
def rerun_short_screen(folder, M, gaps):
    p = os.path.join(folder, "做空筛选.py")
    if not os.path.exists(p) or not M:
        gaps.append("没找到 做空筛选.py 或 指标.json，做空筛选块没刷新")
        return None
    try:
        spec = importlib.util.spec_from_file_location("做空筛选_mod", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "calc_stop_target") or not hasattr(mod, "资金费率读法"):
            gaps.append("文件夹里的 做空筛选.py 不是 v6.4（缺 calc_stop_target / 资金费率读法），请换成 v6.4 版")
        return mod.扫全部(M, verbose=False)
    except Exception as e:      # noqa
        gaps.append(f"做空筛选重跑失败：{e}")
        return None


# ============ 4. 成交额排行（5 亿门槛）重建 ============
def _norm_base(b):
    for pre in ("1000000", "100000", "10000", "1000"):
        if b.startswith(pre) and len(b) > len(pre):
            return b[len(pre):]
    return b


def build_turnover_rank(old_block, gaps, floor=MCAP_FLOOR, top_n=TOP_N):
    resident = (old_block or {}).get("常驻名单") or RESIDENT
    # 币安永续（TRADING 的 USDT 本位）：基础币名 -> 价格倍数（1000PEPE 的价格是 PEPE 的 1000 倍）
    ei = None
    for base in FAPI_BASES:
        try:
            ei = _get(f"{base}/fapi/v1/exchangeInfo")
            break
        except Exception:       # noqa
            continue
    if ei is None:
        raise RuntimeError("币安合约 exchangeInfo 取不到")
    perp = {}
    for x in ei["symbols"]:
        if x.get("contractType") == "PERPETUAL" and x.get("status") == "TRADING" and x.get("quoteAsset") == "USDT":
            b = x["baseAsset"]
            nb = _norm_base(b)
            perp[x["symbol"]] = (nb, (int(b[:len(b) - len(nb)]) if b != nb else 1))
    # 24h 成交额：用合约（永续）24h 成交额，和 成交额排行榜.py 的口径一致（旧榜 ZEC 15 亿是合约口径，现货只有 2 亿左右）
    tick = None
    for base in FAPI_BASES:
        try:
            tick = _get(f"{base}/fapi/v1/ticker/24hr", timeout=40)
            break
        except Exception:       # noqa
            continue
    if tick is None:
        raise RuntimeError("币安合约 24h 成交额取不到")
    rows = []
    for t in tick:
        if t["symbol"] not in perp:
            continue
        base, mult = perp[t["symbol"]]
        if base in STABLES or base in resident:
            continue
        rows.append((base, float(t["quoteVolume"]), float(t["lastPrice"]) / mult))
    rows.sort(key=lambda x: -x[1])
    # 市值（CoinGecko，前 1000 名；同 symbol 取市值最大者）
    cg = {}
    for page in range(1, 5):
        try:
            data = _get(f"{CG_URL}?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}&sparkline=false",
                        timeout=40, retries=3)
        except Exception as e:      # noqa
            if page == 1:
                raise RuntimeError(f"CoinGecko 市值取不到：{e}")
            gaps.append(f"CoinGecko 第 {page} 页没取到（多半是限速），排在 {250*(page-1)} 名之后、市值可能仍高于门槛的币没纳入")
            break
        caps = [c["market_cap"] for c in data if c.get("market_cap")]
        for c in data:
            sym = c["symbol"].upper()
            if c.get("market_cap") and (sym not in cg or c["market_cap"] > cg[sym][0]):
                cg[sym] = (c["market_cap"], c["id"])
        if caps and min(caps) < floor:      # 这一页已经排到门槛以下，后面的页不用再取
            break
        time.sleep(2.5)
    cand = []
    for base, qv, px in rows:
        mc = cg.get(base)
        if not mc or mc[0] < floor:
            continue
        item = {"标的": base, "24h成交额_美元": round(qv), "现价": px, "市值_美元": round(mc[0]),
                "coingecko_id": mc[1]}
        if mc[0] < 1_000_000_000:
            item["门槛档"] = "5-10亿（v6.5新增档）"
        cand.append(item)
        if len(cand) >= top_n:
            break
    block = {"生成时间_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
             "市值门槛_美元": floor, "常驻名单": resident, "候选": cand,
             "来源": "L1L4日更.py：币安永续24h成交额 + CoinGecko市值（同symbol取市值最大者）"}
    # 和旧榜对账：第一次实盘跑时用来确认重建口径与主脚本一致
    if old_block and old_block.get("候选"):
        old = [c["标的"] for c in old_block["候选"]]
        new = [c["标的"] for c in cand]
        lost = [x for x in old if x not in new]
        added = [x for x in new if x not in old]
        block["与旧榜对账"] = {"旧榜有新榜无": lost, "新榜有旧榜无": added}
    return block


# ============ 5. 送审稿四行 ============
def _m(site, k):
    return ((site or {}).get("metrics") or {}).get(k)


def _v(site, k):
    m = _m(site, k)
    return None if m is None else m.get("value")


def _d(site, k):
    m = _m(site, k)
    return "无" if m is None else m.get("display", "无")


def _pp(x):
    return "无" if x is None else f"{x:+.2f}pp"


def l1_discounts(site):
    """按规格 v6.5 第 08 节判两个折扣条件。返回 dict。"""
    nl, usd = _v(site, "netliq13"), _v(site, "usd13")
    ry, baa, nfci, oz = _v(site, "real13"), _v(site, "baa13"), _v(site, "nfci13"), _v(site, "optimism_z")
    net_hit = (nl is not None and usd is not None and nl <= NETLIQ_TH and usd > 0)
    surge = ry is not None and ry >= REAL_TH
    legs = {"BAA走阔": baa is not None and baa > 0, "NFCI转紧": nfci is not None and nfci > 0,
            "乐观度z<0": oz is not None and oz < 0}
    real_hit = surge and any(legs.values())
    edge = []
    if nfci is not None and 0 < nfci < 0.05:
        edge.append(f"NFCI 13周仅 {nfci:+.3f}，刚过零线，边缘触发")
    if baa is not None and 0 < baa < 0.03:
        edge.append(f"BAA 13周仅 {baa:+.2f}pp，边缘触发")
    complete = all(x is not None for x in (nl, usd, ry, baa, nfci, oz))
    return {"数据完整": complete, "净流动性条件": net_hit, "实际利率急升": surge, "实际利率附加条件": legs,
            "实际利率条件": real_hit, "边缘提示": edge,
            "新开仓倍数": 0.7 if (net_hit or real_hit) else 1.0,
            "不加仓": bool(surge and not real_hit)}


def four_lines(site, l2, l3, btc_f):
    L = {}
    lay = (site or {}).get("layers", {})
    dis = l1_discounts(site)
    ry_level = _d(site, "real13")
    L["L1"] = (f"L1：{lay.get('L1', {}).get('verdict_zh', '无')}｜实际利率 {ry_level}、13周 {_pp(_v(site, 'real13'))}"
               f"｜BAA 13周 {_d(site, 'baa13')}、高收益利差 13周 {_d(site, 'hy13')}｜NFCI 13周 {_d(site, 'nfci13')}"
               f"｜乐观度z {_d(site, 'optimism_z')}｜净流动性 13周 {_d(site, 'netliq13')}、美元 13周 {_d(site, 'usd13')}"
               f"｜稳定币(剔除支付机构) 13周 {_d(site, 'stable_expay13')}、交易核心剔除Tron 13周 {_d(site, 'stable_bullets13')}"
               f"｜ETF 4周 {_d(site, 'etf28')}｜BTC–纳指52周相关 {_d(site, 'btc_ndx_corr')}"
               + (f"｜折扣判定：净流动性{'触发' if dis['净流动性条件'] else '未触发'}，实际利率{'触发' if dis['实际利率条件'] else '未触发'}"
                  f"→ 新开仓 ×{dis['新开仓倍数']}" + ("；实际利率急升未配齐附加条件→不加仓" if dis["不加仓"] else "")
                  if dis["数据完整"] else "｜折扣判定：L1 读数不全，无法判定（不要按「未触发」处理）")
               + ("（" + "；".join(dis["边缘提示"]) + "）" if dis["边缘提示"] else ""))
    if l2:
        st, cy = l2.get("state", {}), l2.get("cycle", {})
        L["L2"] = (f"L2：{st.get('name_zh', '无')}｜第 {st.get('days', '无')} 天（自 {st.get('since', '无')}）"
                   f"｜失效线 {cy.get('cycle_low', 0):,.0f}（收盘跌破本轮最低点即退回熊市）｜现价 {l2.get('price', 0):,.0f}"
                   f"，距失效线 {(l2.get('price', 0) / cy['cycle_low'] - 1) * 100:+.1f}%" if cy.get("cycle_low") else
                   f"L2：{st.get('name_zh', '无')}｜第 {st.get('days', '无')} 天")
    else:
        L["L2"] = "L2：无数据"
    if l3:
        sth, p1 = l3.get("sth", {}), l3.get("p1s", {})
        w = l3.get("warning", {})
        vol, conc = w.get("vol30"), w.get("conc")
        calc_on = (vol is not None and conc is not None and vol < VOL_TH and conc >= CONC_TH)
        note = "" if calc_on == bool(w.get("on")) else "（注意：网站标记与按阈值重算不一致，以重算为准）"
        L["L3"] = (f"L3：STH 成本 {sth.get('value', 0):,.0f}（{sth.get('pct_from_price', 0):+.1f}%）"
                   f"｜+1σ 压力区 {p1.get('value', 0):,.0f}（{p1.get('pct_from_price', 0):+.1f}%）"
                   f"｜大波动预警 {'亮' if calc_on else '不亮'}（30天波动 {vol}% / ±5%筹码集中度 {conc}%）{note}")
    else:
        L["L3"] = "L3：无数据"
    f30 = (btc_f or {}).get("fund_30d_ann")
    f7 = (btc_f or {}).get("fund_7d_ann")
    src = (btc_f or {}).get("fund_30d_src")
    sign = "无数据" if f30 is None else ("为负→不开新空，L2非熊市可在L1预算内分批加" if f30 < 0 else "不为负→L4不改动作")
    L["L4"] = (f"L4：BTC 费率30日均 {('%.1f%%' % f30) if f30 is not None else '无'}（{sign}；口径{src or '无'}）"
               f"｜7日均 {('%.1f%%' % f7) if f7 is not None else '无'}"
               f"｜恐慌贪婪 {_d(site, 'fng')}（{(_m(site, 'fng') or {}).get('zone_label_zh', '无')}）"
               f"｜HL费率 {_d(site, 'hl_funding')}、OI 7日 {_d(site, 'hl_oi')}｜山寨季 {_d(site, 'altseason')}、BTC.D {_d(site, 'btc_dom')}"
               f"｜其余只作温度描述")
    return L, dis


def freshness(site, l2, l3, gaps):
    t = _utc_today()
    for name, obj, key in (("site_latest", site, "as_of"), ("l2_latest", l2, "data_date"), ("l3_latest", l3, "data_date")):
        if obj and obj.get(key) != t:
            gaps.append(f"{name} 的日期是 {obj.get(key)}，不是今天 {t}（网站当天还没更新，或取到了旧文件）")


# ============ 主流程 ============
def collect(folder, M, old_rank=None, rank=True):
    """给 每日取数.py 直接调用：M 是已经 load 好的 指标.json（dict）。
    会原地往 M 里补 fund_30d_ann / fund_7d_ann / fund_30d_src（所以必须在跑 做空筛选 之前调用），
    并把 指标.json 存回去。返回 dict：站点读数、费率、成交额排行新块、四行、折扣判定、缺口。
    本函数不碰 每日汇总.json，由调用方合并。"""
    gaps = []
    web = fetch_site(folder, gaps)
    site, l2, l3 = web.get("site_latest.json"), web.get("l2_latest.json"), web.get("l3_latest.json")
    freshness(site, l2, l3, gaps)

    fund = {}
    try:
        fund = fetch_funding(folder, M, gaps)
    except Exception as e:      # noqa
        gaps.append(f"费率30日均整步失败：{e}")

    rank_block = None
    if rank:
        try:
            rank_block = build_turnover_rank(old_rank, gaps)
        except Exception as e:      # noqa
            gaps.append(f"成交额排行没按 5 亿重建，沿用 成交额排行榜.py 的结果（门槛以那个脚本里写的为准）：{e}")

    lines, dis = four_lines(site, l2, l3, fund.get("BTC"))
    l1l4 = {"生成时间_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "四行": lines, "L1折扣判定": dis,
            "网站日期": {"site": (site or {}).get("as_of"), "l2": (l2 or {}).get("data_date"),
                       "l3": (l3 or {}).get("data_date")},
            "BTC费率": fund.get("BTC"), "缺口": gaps}
    _save(folder, "L1L4读数.json", l1l4)
    txt = "\n".join(lines[k] for k in ("L1", "L2", "L3", "L4"))
    if gaps:
        txt += "\n\n已知数据缺口：\n" + "\n".join("· " + g for g in gaps)
    with open(os.path.join(folder, "送审稿四行.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    return {"L1L4": l1l4, "成交额排行": rank_block, "文本": txt}


def run(folder=None, rank=True):
    """单独运行（不经过 每日取数.py）时用：直接补丁 指标.json / 每日汇总.json。"""
    folder = folder or os.path.dirname(os.path.abspath(__file__))
    M = _load(folder, "指标.json")
    summary = _load(folder, "每日汇总.json") or {}
    gaps0 = []
    if M is None:
        gaps0.append("运行目录里没有 指标.json：只下载网站读数，不算每币费率、不刷新做空筛选")
        M = {}
    r = collect(folder, M, old_rank=summary.get("成交额排行"), rank=rank)
    r["L1L4"]["缺口"] = gaps0 + r["L1L4"]["缺口"]
    if M:
        scr = rerun_short_screen(folder, M, r["L1L4"]["缺口"])
        if scr is not None:
            summary["指标"] = M
            summary["做空筛选"] = scr
    if r["成交额排行"]:
        summary["成交额排行"] = r["成交额排行"]
    summary["L1L4"] = r["L1L4"]
    _save(folder, "每日汇总.json", summary)
    print(r["文本"])
    if gaps0:
        print("\n".join("· " + g for g in gaps0))
    print_rank_diff(summary.get("成交额排行"))
    return summary


def print_rank_diff(rk):
    rk = rk or {}
    if rk.get("与旧榜对账"):
        print("\n成交额榜对账（门槛 %s）：%s" % (rk.get("市值门槛_美元"), json.dumps(rk["与旧榜对账"], ensure_ascii=False)))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
