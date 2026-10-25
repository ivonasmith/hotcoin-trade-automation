# -*- coding: utf-8 -*-
"""热币分析 · 周日「下周清单」海报生成器（事件预览 + 本周总结版，不含订单）。
沿用《热币海报标准》的色板/字体/落款；工作日「今日方向总表」生成器不动。
用法：改下面的 DATA，然后  python3 weekend_gen.py <输出目录>
中文、英文各一张竖版海报（1080px 宽，截图 scale 2）。"""
import re
import sys
from pathlib import Path

# ───────────────────────── 每周只改这里 ─────────────────────────
DATA = dict(
    issue_date="2026-10-25",
    week_zh="10-26 周一 → 11-01 周日", week_en="Mon Oct 26 → Sun Nov 1",
    recap_zh="10-19 → 10-25", recap_en="Oct 19 → Oct 25",
    acct_pct="+6.84%", acct_cum="+$683.72", closed_n=17,
    # 持仓浮盈：按 10-24 报价核算（名义账户，未平仓不计入战绩）
    floating=[dict(sym="SOL", entry=106.5433, px=98.91, notional=233.33, r="+3.1R", stop="105.61"),
              dict(sym="XRP", entry=1.3724, px=1.2569, notional=333.33, r="+3.7R", stop="1.3207")],
)
SIGNATURE = "链上大叔｜流动性到 Alpha"; SIGNATURE_EN = "Onchain Uncle | Liquidity to Alpha"; HANDLE = "@Uncle_Onchain"

EVENTS = [  # 日期, 事件(zh), 事件(en), 地点(zh), 地点(en)   —— 数据来自 uncleonchain.com/calendar（2026-10-25 读取）
    ("10-27→29", "Swell 2026（Ripple 年会）", "Swell 2026 (Ripple)", "纽约", "New York"),
    ("10-27→29", "MERGE Madrid 2026", "MERGE Madrid 2026", "马德里", "Madrid"),
    ("10-27→29", "Zcon 7（Zcash）", "Zcon 7 (Zcash)", "坎昆", "Cancún"),
    ("10-28→29", "Meridian 2026", "Meridian 2026", "里斯本", "Lisbon"),
    ("10-30→31", "LABITCONF 26", "LABITCONF 26", "布宜诺斯艾利斯", "Buenos Aires"),
    ("10-31→11-01", "BTC Balkans 2026", "BTC Balkans 2026", "索非亚", "Sofia"),
    ("11-01→02", "India Blockchain Week 2026", "India Blockchain Week 2026", "孟买", "Mumbai"),
]
HACK = [
    ("10-31→12-06", "Arbitrum Open House Dubai 线上黑客松", "Arbitrum Open House Dubai (online buildathon)"),
    ("11-01→11-12", "Solana Hacker House London", "Solana Hacker House London"),
    ("11-01→11-14", "NEAR 线上黑客松", "NEAR Hackathon (online)"),
]


def floating_total():
    tot = 0.0
    for f in DATA["floating"]:
        tot += f["notional"] * (f["entry"] - f["px"]) / f["entry"]   # 全部是空单
    return tot


TXT = {
 "zh": dict(
  kicker="热币分析 · 周日 · {d} · 下周清单", title="下周清单", sub=DATA["week_zh"],
  t1v="10-29 02:00", t1l="美联储议息（北京时间）",
  t2v="±2 小时", t2l="公布前后波动一般更大",
  t3v=DATA["acct_pct"], t3l="名义账户累计收益",
  t4v="+${:.0f}".format(floating_total()), t4l="持仓浮盈（10-24 报价，未计入）",
  s1="下周大事件", s1b="外部风险",
  e1h="美联储议息决议", e1t="北京时间 10-29（周四）凌晨 2:00 · 美东 10-28 14:00",
  e1p="利率会议。我们的日历统计显示：公布前后两小时，BTC 的振幅一般比平时大，但往哪边走很难猜。",
  s2="该避开什么 · 该盯什么",
  avoid_h="避开", avoid=["10-29 凌晨 0:00–4:00（北京时间）不挂贴近现价的单", "不加杠杆去追这一晚的单边行情"],
  watch_h="盯住", watch=["议息当天的日线收盘在 10-29 早上 8:00（北京时间），先看收盘，不凭一根影线下结论", "已在场的单子，止损按收盘价判定，盘中扫一下不算"],
  s3="本周行业活动", s3n="会议扎堆的一周，项目方爱在这几天放消息；会议本身不是交易信号，只是消息多发期。",
  hack_h="线上与黑客松",
  s4="本周总结", s4n=DATA["recap_zh"],
  r1="新增 3 张空单：SOL、XRP、BTC", r2="2 张成交（SOL、XRP，10-21），1 张 BTC 空单还在等价格",
  r3="1 张 ETH 空单没成交就撤了：成交前资金费率转负，这是挂单时就写好的撤单条件",
  r4="持仓：SOL 浮盈 +3.1R，XRP 浮盈 +3.7R（10-24 报价）；两张的止损都已下移到进场价之下，最差也是小赚。XRP 离目标价只差不到 1%，我们没有提前平仓，也不手动加减",
  r5="已了结 {n} 笔，累计 {c}，账户收益 {p}。本周没有新平仓的单，浮盈在平仓前都不算数。",
  honest="名义账户，不是作者真实持仓；浮盈不等于落袋；已了结样本只有 {n} 笔，别当成稳赚。",
  foot="名义账户，不代表作者真实持仓｜浮盈在平仓前不计入战绩｜止损一律按收盘价判定｜不构成投资建议",
  sig=SIGNATURE, src="日历数据：uncleonchain.com/calendar", unit_d="天"),
 "en": dict(
  kicker="Hot Coin Desk · Sunday · {d} · Week Ahead", title="Week Ahead", sub=DATA["week_en"],
  t1v="Oct 29 02:00", t1l="Fed decision (Beijing time)",
  t2v="±2 hours", t2l="Swings usually larger around release",
  t3v=DATA["acct_pct"], t3l="Paper account, since launch",
  t4v="+${:.0f}".format(floating_total()), t4l="Open profit (Oct 24 prices, not booked)",
  s1="Biggest event next week", s1b="External risk",
  e1h="Fed rate decision", e1t="Thu Oct 29, 02:00 Beijing · Wed Oct 28, 2:00 pm ET",
  e1p="Rate meeting. In our calendar stats, BTC's range in the two hours around the release is usually wider than normal, but the direction is hard to call.",
  s2="What to avoid · What to watch",
  avoid_h="Avoid", avoid=["No limit orders close to price from 00:00 to 04:00 on Oct 29 (Beijing time)", "Don't chase a one-way move that night with leverage"],
  watch_h="Watch", watch=["The daily close for decision day is 8:00 am Oct 29 Beijing time. Read the close, not a single wick", "Open positions: stops are judged on the close; an intraday poke doesn't count"],
  s3="Industry events this week", s3n="A crowded conference week means projects tend to time announcements. A conference is not a trade signal, just a busier news cycle.",
  hack_h="Online and hackathons",
  s4="Weekly recap", s4n=DATA["recap_en"],
  r1="3 new short orders: SOL, XRP, BTC", r2="2 filled (SOL, XRP on Oct 21); the BTC short is still waiting for price",
  r3="1 ETH short was cancelled unfilled: funding turned negative before the fill, a cancel condition written when the order was posted",
  r4="Open: SOL +3.1R, XRP +3.7R (Oct 24 prices). Both stops are already below entry (shorts), so the worst case is a small win. XRP is within 1% of target and we did not close early or tinker by hand",
  r5="{n} closed trades, cumulative {c}, account return {p}. Nothing was closed this week, and open profit doesn't count until it is closed.",
  honest="Paper account, not the author's real positions. Open profit is not banked. Only {n} closed trades so far, so don't read this as a sure thing.",
  foot="Paper account, not the author's real positions | Open profit not counted until closed | Stops judged on the close | Not investment advice",
  sig=SIGNATURE_EN, src="Calendar data: uncleonchain.com/calendar", unit_d="d"),
}

CSS = """
:root{--ink:#1b1b1b;--brown:#6f5b4b;--muted:#8a7a6a;--hair:#e6e0d3;--olive:#8C6C1F;--gray:#8A8477;--blue:#1A6694;
 --green:#1C7A4A;--red:#C02128;
 --serif:Georgia,'Times New Roman','Noto Serif CJK SC','Songti SC',serif;
 --mono:'JetBrains Mono','Liberation Mono','Courier New',monospace;
 --sans:-apple-system,'PingFang SC','Noto Sans CJK SC','Microsoft YaHei',Helvetica,Arial,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--sans);color:var(--ink);background:#fff;width:1080px}
.bar{height:11px;background:#146290}
.wrap{padding:34px 40px 30px}
.kicker{font-family:var(--mono);font-weight:700;color:var(--olive);letter-spacing:.5px;font-size:21px}
h1{font-family:var(--serif);font-weight:500;font-size:62px;margin-top:6px;line-height:1.1}
.sub{font-family:var(--mono);font-size:24px;color:var(--brown);margin-top:8px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:26px}
.tile{background:#F9F7F2;border:1px solid var(--hair);padding:16px 14px 14px}
.tile .v{font-family:var(--mono);font-weight:800;font-size:31px;color:var(--blue);line-height:1.1;white-space:nowrap}
.tile .l{font-size:19px;color:var(--gray);margin-top:8px;line-height:1.3}
.sec{margin-top:30px}
.sech{display:flex;align-items:baseline;gap:14px;margin-bottom:12px}
.sech .t{font-family:var(--serif);font-weight:700;font-size:38px}
.sech .n{font-size:20px;color:var(--muted)}
.card{border:1px solid var(--hair);padding:22px 24px}
.card.red{border-left:7px solid var(--red);background:#FDF6F5}
.card.gold{border-left:7px solid #E3CD84;background:#FFFBEF}
.badge{display:inline-block;font-size:18px;font-weight:700;color:#fff;background:var(--red);padding:3px 14px;border-radius:999px;vertical-align:middle}
.eh{font-family:var(--serif);font-weight:700;font-size:36px}
.et{font-family:var(--mono);font-size:24px;color:var(--brown);margin:8px 0 10px}
.ep{font-size:26px;line-height:1.6;color:#333}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.box{padding:20px 22px;border:1px solid var(--hair)}
.box.a{background:#FDF6F5}.box.w{background:#F3F8FB}
.box h3{font-size:28px;font-weight:700;margin-bottom:10px}
.box.a h3{color:var(--red)}.box.w h3{color:var(--blue)}
.box li{font-size:23px;line-height:1.55;color:#333;margin:0 0 8px 20px}
.ev{width:100%;border-collapse:collapse}
.ev td{padding:11px 6px;border-bottom:1px solid var(--hair);font-size:24px;vertical-align:top}
.ev td.d{font-family:var(--mono);font-size:21px;color:var(--brown);white-space:nowrap;width:190px}
.ev td.c{color:var(--muted);text-align:right;font-size:21px;white-space:nowrap}
.note{font-size:20px;color:var(--muted);margin-top:10px;line-height:1.5}
.sum li{font-size:25px;line-height:1.6;color:#222;margin:0 0 10px 24px}
.honest{margin-top:14px;font-size:21px;color:var(--brown);background:#F9F7F2;border:1px solid var(--hair);padding:12px 16px;line-height:1.5}
.foot{margin-top:28px;padding-top:16px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;gap:20px;align-items:flex-end}
.foot .l{font-size:17px;color:var(--gray);line-height:1.5;flex:1 1 auto;min-width:0}
.foot .r{text-align:right;white-space:nowrap}
.foot .s{font-family:var(--serif);font-size:28px;font-weight:700}
.foot .h{font-family:var(--mono);font-size:17px;color:var(--gray)}
"""


def build(lang):
    t = TXT[lang]
    z = lang == "zh"
    ev_rows = "".join(
        f'<tr><td class="d">{d}</td><td>{(zn if z else en)}</td><td class="c">{(zc if z else ec)}</td></tr>'
        for d, zn, en, zc, ec in EVENTS)
    hack_rows = "".join(f'<tr><td class="d">{d}</td><td colspan="2">{(a if z else b)}</td></tr>' for d, a, b in HACK)
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)
    sums = [t["r1"], t["r2"], t["r3"], t["r4"], t["r5"].format(n=DATA["closed_n"], c=DATA["acct_cum"], p=DATA["acct_pct"])]
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="bar"></div><div class="wrap">
<div class="kicker">{t['kicker'].format(d=DATA['issue_date'])}</div>
<h1>{t['title']}</h1><div class="sub">{t['sub']}</div>
<div class="tiles">
 <div class="tile"><div class="v">{t['t1v']}</div><div class="l">{t['t1l']}</div></div>
 <div class="tile"><div class="v">{t['t2v']}</div><div class="l">{t['t2l']}</div></div>
 <div class="tile"><div class="v">{t['t3v']}</div><div class="l">{t['t3l']}</div></div>
 <div class="tile"><div class="v">{t['t4v']}</div><div class="l">{t['t4l']}</div></div>
</div>
<div class="sec"><div class="sech"><span class="t">{t['s1']}</span></div>
 <div class="card red"><div><span class="eh">{t['e1h']}</span> &nbsp;<span class="badge">{t['s1b']}</span></div>
 <div class="et">{t['e1t']}</div><div class="ep">{t['e1p']}</div></div></div>
<div class="sec"><div class="sech"><span class="t">{t['s2']}</span></div>
 <div class="two"><div class="box a"><h3>{t['avoid_h']}</h3><ul>{li(t['avoid'])}</ul></div>
 <div class="box w"><h3>{t['watch_h']}</h3><ul>{li(t['watch'])}</ul></div></div></div>
<div class="sec"><div class="sech"><span class="t">{t['s3']}</span></div>
 <table class="ev">{ev_rows}</table>
 <div style="font-size:22px;font-weight:700;color:var(--brown);margin-top:16px">{t['hack_h']}</div>
 <table class="ev">{hack_rows}</table>
 <div class="note">{t['s3n']}</div></div>
<div class="sec"><div class="sech"><span class="t">{t['s4']}</span><span class="n">{t['s4n']}</span></div>
 <div class="card gold"><ul class="sum">{li(sums)}</ul></div>
 <div class="honest">{t['honest'].format(n=DATA['closed_n'])}</div></div>
<div class="foot"><div class="l">{t['foot']}<br>{t['src']}</div><div class="r"><div class="s">{t['sig']}</div><div class="h">{HANDLE}</div></div></div>
</div></body></html>"""


def check(htmls):
    """自检：英文版不许出现中文；关键数字存在。"""
    problems = []
    text_en = re.sub(r"<style>.*?</style>", "", htmls["en"], flags=re.S)
    if re.search(r"[一-鿿]", text_en):
        problems.append("英文版出现中文字符")
    for lang, h in htmls.items():
        for k in ("02:00", DATA["acct_pct"], "+3.1R", "+3.7R"):
            if k not in h:
                problems.append(f"{lang}缺少 {k}")
    return problems


def render(outdir):
    htmls = {l: build(l) for l in ("zh", "en")}
    p = check(htmls)
    if p:
        raise SystemExit("自检未通过：" + "；".join(p))
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    files = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for lang, h in htmls.items():
            (out / f"weekend_{lang}.html").write_text(h, encoding="utf-8")
            pg = br.new_page(viewport={"width": 1080, "height": 1200}, device_scale_factor=2)
            pg.set_content(h); pg.wait_for_timeout(300)
            f = out / f"poster_{lang}_周日下周清单_{DATA['issue_date']}.png"
            pg.screenshot(path=str(f), full_page=True); pg.close(); files.append(f)
        br.close()
    return files


if __name__ == "__main__":
    print(floating_total())
    for f in render(sys.argv[1] if len(sys.argv) > 1 else "."):
        print(f)
