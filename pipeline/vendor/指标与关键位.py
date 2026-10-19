#!/usr/bin/env python3
"""热币分析 · 自算指标 + 历史关键位（角色转换版）
用法: python3 指标与关键位.py            (读 raw.json)
输出: /home/user/工具箱/指标.json + 终端可读报告

⚠️ 方法铁律 (2026-08-24 修正):
  绝不按「是否被收盘穿越」过滤位置 —— 那会系统性删掉全部角色转换位。
  正确做法: 统计整条历史里价格与某价格带发生过多少「接触段」,
  并区分每段从上方来(当支撑)还是从下方来(当压力)。角色会翻转,位置不会消失。
⚠️ 截尾年龄陷阱: 首次接触落在数据起点上 → trunc=True, 该位置禁止按「老位置」加成下仓。
⚠️ 数据跨度不足 250 天的币 → 禁用慢档日线,只做快档 4h。
"""
import json, datetime

import os
_H = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(_H, 'raw.json')
OUT = os.path.join(_H, '指标.json')
近期根数 = 20      # 每个币额外带最近 N 根日线与 4h，动态止盈的枯竭信号要靠它判

def f(k):
    return dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5]))

def rsi(cl, n=14):
    """数据不足 n+1 根就返回 None——新上市的币日线可能只有几根，
    以前这里直接 IndexError，一个新币能把整天的取数带崩。"""
    if cl is None or len(cl) < n+1:
        return None
    g = l = 0.0
    for i in range(1, n+1):
        d = cl[i]-cl[i-1]; g += max(d,0); l += max(-d,0)
    g /= n; l /= n
    for i in range(n+1, len(cl)):
        d = cl[i]-cl[i-1]
        g = (g*(n-1)+max(d,0))/n; l = (l*(n-1)+max(-d,0))/n
    return 100 if l == 0 else 100-100/(1+g/l)

def atr(b, n=14):
    if not b or len(b) < n+1:
        return None
    tr = [max(b[i]['h']-b[i]['l'], abs(b[i]['h']-b[i-1]['c']), abs(b[i]['l']-b[i-1]['c'])) for i in range(1, len(b))]
    a = sum(tr[:n])/n
    for x in tr[n:]: a = (a*(n-1)+x)/n
    return a

def ma(cl, n):
    """不足 n 根返回 None——不能拿 5 根均价冒充 MA20"""
    return sum(cl[-n:])/n if cl and len(cl) >= n else None


def chg(cl, k):
    """k 根之前到现在的涨跌幅，不够就 None"""
    return (cl[-1]/cl[-1-k]-1)*100 if cl and len(cl) > k else None


def r2(v, n=2):
    return round(v, n) if v is not None else None


def pct(v, base, n=2):
    return round(v/base*100, n) if (v is not None and base) else None

def levels(bars, price, tol=0.02, min_segs=3, seed_band=True):
    """极值点聚成价格带, 统计接触段。seed_band=True 用固定带宽防止链式合并成巨簇。"""
    piv = []
    for i in range(3, len(bars)-3):
        w = bars[i-3:i+4]
        if bars[i]['h'] == max(x['h'] for x in w): piv.append(bars[i]['h'])
        if bars[i]['l'] == min(x['l'] for x in w): piv.append(bars[i]['l'])
    if not piv: return []
    piv.sort()
    clus = []; cur = [piv[0]]
    for p in piv[1:]:
        ref = cur[0] if seed_band else cur[-1]   # 固定带宽: 与簇内第一个比
        if p/ref-1 < tol: cur.append(p)
        else: clus.append(cur); cur = [p]
    clus.append(cur)
    res = []
    for cl in clus:
        lo, hi = min(cl), max(cl); mid = sum(cl)/len(cl)
        # last_seg = 最后一个接触段的【起点】；last_touch = 最后一次真正碰到的【那一天】。
        # 两者必须分开。旧版只有前者，还把它当成 gap 的基准，于是：
        # 价格正贴着某条位置来回磨（一个长接触段），每多磨一天 gap 就大一天，
        # 明明今天还在碰，脚本却报「19 天没碰过」。
        # 「刚被碰过 ≤15 天 → 三分之一仓」这条规则读的就是 gap，
        # 也就是说它在最需要打折的位置上，恰好给出满仓。方向是反的，不是偏一点。
        segs = days = sup = resi = 0; prev = False
        first = last_seg = last_touch = None
        for i, b in enumerate(bars):
            touch = b['l'] <= hi*1.005 and b['h'] >= lo*0.995
            if touch:
                days += 1
                last_touch = b['t']
                if not prev:
                    segs += 1
                    if first is None: first = b['t']
                    last_seg = b['t']
                    if i > 0:
                        if bars[i-1]['c'] > hi: sup += 1
                        elif bars[i-1]['c'] < lo: resi += 1
                prev = True
            else:
                prev = False
        if segs < min_segs: continue
        res.append(dict(
            mid=round(mid,8), lo=round(lo,8), hi=round(hi,8), segs=segs, days=days,
            age=round((bars[-1]['t']-first)/86400000),
            gap=round((bars[-1]['t']-last_touch)/86400000),   # ← 改成从最后一次接触算
            gap_seg=round((bars[-1]['t']-last_seg)/86400000),  # 旧口径，留着做对照
            活跃=(bars[-1]['l'] <= hi*1.005 and bars[-1]['h'] >= lo*0.995),
            sup=sup, res=resi, trunc=(first == bars[0]['t']),
            dist=round((mid/price-1)*100, 2),
            role=('支撑' if mid < price else '压力'),
            first=datetime.datetime.fromtimestamp(first/1000, datetime.timezone.utc).strftime('%Y-%m'),
            last=datetime.datetime.fromtimestamp(last_touch/1000, datetime.timezone.utc).strftime('%Y-%m-%d'),
            last_seg=datetime.datetime.fromtimestamp(last_seg/1000, datetime.timezone.utc).strftime('%Y-%m-%d')))
    return res

def analyse(raw):
    rep = {}
    for c, d in raw.items():
        b1 = [f(k) for k in d['d1']]; b4 = [f(k) for k in d['h4']]
        cl1 = [x['c'] for x in b1]; cl4 = [x['c'] for x in b4]
        price = cl4[-1]
        fr = d['prem']
        per = 8 if (int(fr['nextFundingTime'])-int(fr['time'])) > 4*3600*1000 else 4
        ann = float(fr['lastFundingRate'])*(24/per)*365*100
        m200 = ma(cl1, 200)
        atr_d, atr_4 = atr(b1), atr(b4)
        high120 = max(x['h'] for x in b1[-120:])
        span = (b1[-1]['t']-b1[0]['t'])/86400000
        rep[c] = dict(
            price=price, rsi_d=r2(rsi(cl1),1), rsi_4=r2(rsi(cl4),1),
            atr_d=r2(atr_d,8), atr_d_pct=pct(atr_d, price),
            atr_4=r2(atr_4,8), atr_4_pct=pct(atr_4, price),
            ma20=r2(ma(cl1,20),6), ma50=r2(ma(cl1,50),6),
            ma200=r2(m200,6),
            dev200=round((price/m200-1)*100,1) if m200 else None,
            bars_d=len(b1), span_days=round(span), 慢档可用=(span >= 250),
            fund_ann=round(ann,1), fund_period_h=per,
            oi=float(d['oi']['openInterest']) if d.get('oi') else None,  # 09-22修：原来没做空值防护，某个币OI拿不到时整个脚本崩，跟下面ls那行的写法对齐
            ls=d['ls'][0]['longShortRatio'] if d['ls'] else None,
            chg24=r2(chg(cl1,1)), chg7=r2(chg(cl1,7)), chg30=r2(chg(cl1,30)),
            drawdown120=round((price/high120-1)*100,2),
            阶段=stage(price, m200, price/high120-1),
            d1_below=[x for x in levels(b1[-1600:], price) if -12 < x['dist'] < 0],
            d1_above=[x for x in levels(b1[-1600:], price) if 0 < x['dist'] < 14],
            h4_below=[x for x in levels(b4[-500:], price, 0.012) if -8 < x['dist'] < 0],
            h4_above=[x for x in levels(b4[-500:], price, 0.012) if 0 < x['dist'] < 8],
            # 最近 N 根原始 K 线：长上影线、连续滞涨、RSI 从超买回落，都要靠它算
            近期日线=[[x['t'], x['o'], x['h'], x['l'], x['c'], x['v']] for x in b1[-近期根数:]],
            近期4h=[[x['t'], x['o'], x['h'], x['l'], x['c'], x['v']] for x in b4[-近期根数:]],
            数据不足=[k for k,v in (('RSI14日线',rsi(cl1)),('ATR14日线',atr_d),
                                    ('MA20',ma(cl1,20)),('MA50',ma(cl1,50)),('MA200',m200),
                                    ('7日涨跌',chg(cl1,7)),('30日涨跌',chg(cl1,30))) if v is None],
            rsi_d_近期=[r2(rsi(cl1[:i+1]),1) for i in range(max(0,len(cl1)-近期根数), len(cl1))],
        )
    return rep

def stage(price, m200, dd):
    """做空阶段判定 (2026-08-25 规则)"""
    if not m200: return '数据不足'
    if price < m200: return 'MA200下方'
    if dd > -0.05: return '主升浪(禁止做空)'
    if dd > -0.20: return '宽幅震荡(过三道过滤才可空)'
    return '深回撤(最佳做空环境)'

if __name__ == '__main__':
    rep = analyse(json.load(open(RAW)))
    json.dump(rep, open(OUT,'w'), indent=1, ensure_ascii=False)
    for c, r in rep.items():
        print("="*46)
        print(f"{c}  {r['price']}  24h {r['chg24']}%  7d {r['chg7']}%  30d {r['chg30']}%")
        print(f"  RSI 日{r['rsi_d']} 4h{r['rsi_4']} | ATR 日{r['atr_d_pct']}% 4h{r['atr_4_pct']}%")
        if r['数据不足']:
            print(f"  ⚠️ 日线只有 {r['bars_d']} 根，算不出：{'、'.join(r['数据不足'])}")
        print(f"  MA20 {r['ma20']} MA50 {r['ma50']} MA200 {r['ma200']} 偏离{r['dev200']}%")
        print(f"  费率年化 {r['fund_ann']}%({r['fund_period_h']}h) 多空比 {r['ls']} 120日回撤 {r['drawdown120']}%")
        print(f"  阶段: {r['阶段']} | 日线跨度 {r['span_days']}天 慢档可用 {r['慢档可用']}")
        for tag in ('d1_below','d1_above','h4_below','h4_above'):
            if r[tag]:
                print(f"  {tag}:")
                for x in r[tag]:
                    print(f"    {x['mid']} 距{x['dist']}% 段{x['segs']} 龄{x['age']}天 间隔{x['gap']}天 "
                          f"支{x['sup']}/压{x['res']} 起{x['first']} 末{x['last']}"
                          + ("  ⚠️截尾" if x['trunc'] else ""))
    print("\n已写入", OUT)
