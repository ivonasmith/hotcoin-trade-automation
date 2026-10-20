# -*- coding: utf-8 -*-
"""热币分析 · 今日方向总表 · 4张海报生成器（标准版式 = 第29期定稿 + 《热币海报标准》）
用法：改下面的 DATA / ROWS / CLOSED，然后  python3 poster_gen.py  再  python3 shot.py
版式不许改动：要改版式，先改《热币海报标准》文档，再改这里。
桌面版和手机版共用同一份 ROWS / CLOSED / DATA（防止字段分叉）。"""
import os

# ───────────────────────── 每期只改这里 ─────────────────────────
DATA = dict(
    issue="31", date="2026-10-12",
    leg_day=104, leg_start="2026-06-30", leg_start_close="58,624.71",
    leg_peak_pct="+47.8%", leg_peak_note="09-21最高收盘86,620，未刷新", leg_peak_note_en="Sep 21 peak close 86,620, not exceeded",
    leg_dd="6.92%", leg_dd_date="09-15", leg_past=15,          # 距89天提示线已越过天数
    alt_d0="08-27", alt_d0_close="80,250", alt_day=46, alt_cleared="09-26",
    alt_top3="ZEC+207%、ARB+194%、RAY+185%", alt_top3_en="ZEC +207%, ARB +194%, RAY +185%",
    acct_closed=17, acct_note="更新至第31期·10-12（新增ETH空单挂单1张）", acct_note_en="updated to Issue 31 · 10-12 (1 new ETH short order pending)",
    acct_cum="+$683.72", acct_cur="$10,683.72", acct_pct="+6.84%",
    acct_open=0, acct_pending=1,
    badge_today_zh="今日新增1单", badge_today_en="1 new order",
    today_zh=("BTC站上8.3万，反弹清掉约1.4亿美元空头，费率却只有6.8%，不是杠杆追涨；真正拥挤的空头在山寨——16个盯盘币里6个费率为负，这些币不碰空单。"
              "今天只新增一张$ETH空单：现价已压在20日线、50日线下面，费率+0.3%没有拥挤，限价挂在2,638.15（几乎正压着20日线）。"
              "这是逆着BTC涨腿的小仓位（1/6仓，名义约$333，止损约亏$6.7，到目标约赚$26.7）：今天日线收回50日线（约2,557）上方即作废，成交前费率转负则撤单，"
              "成交后日线收盘站上2,690.91离场。"),
    today_en=("BTC is back above $83k; the rebound liquidated ~$140M of shorts, yet funding is only 6.8% — no leverage chasing. The crowded shorts are in alts: 6 of 16 tracked coins have negative funding, and we don't short those. "
              "One new order today: short $ETH. It already sits under both its 20- and 50-day averages, funding is +0.3% (not crowded), limit 2,638.15 right on the 20-day. "
              "A small counter-trend size against BTC's leg (1/6 size, ~$333 notional, ~$6.7 at the stop, ~$26.7 at target): voided if today's daily close is back above the 50-day (~2,557), cancelled if funding turns negative before fill, out if a daily close lands above 2,690.91 after fill."),
    note_zh="全部在场持仓与挂单都在上表——完整历史清单见交易记录追踪表。做空单的止损只下移、不上移。",
    note_en="All open positions and pending orders are in the table above — full history in the trade log. For short orders the stop only moves down, never up.",
)
# 主表：持仓中永远最前；其余按距现价（正数从小到大，再负数按绝对值从小到大）。同一份数据，桌面/手机共用。
ROWS = [
    dict(sym="ETH", side="空", tf_zh="日线", tf_en="Daily", state="挂单中", state_en="Pending", size="1/6仓", size_en="1/6 size",
         entry="2,638.15", price="2,543.54",
         pnl_zh="未成交", pnl_en="Unfilled", pnl_sub_zh="距现价 +3.72%", pnl_sub_en="+3.72% away", pnl_cls="gray",
         stop="2,690.91", stop_sub="+2.00%", stop_moved=False,
         target="2,427.10", target_sub="-8.00%"),
]
CLOSED = [  # 按平仓日倒序，封顶6张
    ("ETH", "17", "+7.69%", "+50.8 USD"), ("XRP", "9", "+9.07%", "+181.4 USD"), ("SOL", "16", "+21.46%", "+141.6 USD"),
    ("XRP", "17", "-6.16%", "-41 USD"), ("BTC", "5", "-1.18%", "-24 USD"), ("ETH", "14", "-2.02%", "-13 USD"),
]
SIGNATURE = "链上大叔｜流动性到 Alpha"; SIGNATURE_EN = "Onchain Uncle | Liquidity to Alpha"; HANDLE = "@Uncle_Onchain"
FOOTER_ZH = "挂单15天有效，接不到就作废｜已发布方向/进场/目标永不回头改，止损只上移不下调｜总仓位不超账户2倍，最多挂5张｜不构成投资建议"
FOOTER_EN = "Orders valid 15 days, void if unfilled | Published direction/entry/target never revised, stops only move up, never down | Total notional across positions + pending orders capped at 2x equity, max 5 pending orders | Not investment advice"
# ────────────────────────────────────────────────────────────────

T = {  # 文案表（中/英）
 "zh": dict(kicker="热币分析 · 第 {n} 期 · {d} · {dev}", title="今日方向总表", dev_d="电脑版", dev_m="手机版",
   leg_tag="涨腿追踪", leg_title="BTC 第 {n} 天", leg_badge="多头只减不加", leg_sub="起点 <b>{s}</b>（收 {c}）· 距起点最高涨 <b>{p}</b>（{note}）",
   hint="89天提示线", start="起点", cur="第{n}天",
   st1="腿内最大回撤（{d}）", st1n="历史分位 P25=14.5% / 中位=18.5% / P75=22.9%，目前仍在最浅区间",
   st2="已越过89天提示线", st2v="+{n} 天", st2n="假设级：2019年后越过89天的5条腿，之后2–38天内见顶；全样本不成立，只提示，不做闸门",
   alt_tag="山寨轮动窗口", alt_title="D0+{n} 天", alt_badge="已清零", alt_sub="D0=<b>{d0}</b>（BTC收{c}）· 峰值涨幅最高三个：{top}",
   alt_a="只验证时机，不验证选币", alt_av="不给币名单", alt_an="634币·窗口测过：机械选币没跑赢等权篮子（+9.0% vs +12.9%）",
   alt_b="D0+30清零", alt_bv="已清零", alt_bn="{c}已清零。新窗口要等新的D0：BTC收盘10日涨幅≥15%后首次回落",
   alt_end="{c}已清零", d24="D0+24",
   acct_tag="账户战绩", acct_title="开栏至今", acct_line="已了结 {n} 笔 · {note}", acct_lbl="账户收益率（$10,000起始，2倍杠杆）",
   k1="累计盈亏 / 当前账户", k2="持仓中 / 挂单未成交", k3="杠杆", lev="2倍",
   m_k1="累计", m_k2="当前账户", m_k3="持仓/挂单", m_k4="杠杆", m_acct="开栏至今 · 已了结 {n} 笔",
   closed="最近了结的单子", closed_foot="完整战绩见交易记录追踪表", closed_foot_m="完整战绩见交易记录追踪表。口径：名义账户$10,000，每笔等额投入$2,000，2倍杠杆，按币价盈亏折算。",
   issue="第{i}期", issue_dir="第{i}期·做多",
   h=["标的","进场","现价","浮动盈亏","移动止损","下单时目标"], hold="当前持仓", today="今日"),
 "en": dict(kicker="Hot Coin Desk · Issue {n} · {d} · {dev}", title="Today's Direction Board", dev_d="Desktop", dev_m="Mobile",
   leg_tag="Leg Tracker", leg_title="BTC Day {n}", leg_badge="Longs: trim only", leg_sub="Start <b>{s}</b> (close {c}) · Peak gain from start <b>{p}</b> ({note})",
   hint="Day 89 hint", start="Start", cur="Day {n}",
   st1="Max drawdown within leg ({d})", st1n="History: P25=14.5% / median=18.5% / P75=22.9%, still the shallowest range",
   st2="Past the day-89 hint", st2v="+{n} d", st2n="Soft hypothesis: since 2019, all 5 legs past day 89 topped within 2–38 days; fails on full history. Hint only, not a gate",
   alt_tag="Alt Rotation", alt_title="D0+{n} Days", alt_badge="Cleared", alt_sub="D0=<b>{d0}</b> (BTC closed {c}) · Top 3 peak gains: {top}",
   alt_a="Timing only, no picks", alt_av="No coin list", alt_an="634 coin-windows tested: mechanical picks didn't beat the equal-weight basket (+9.0% vs +12.9%)",
   alt_b="D0+30 zero-out", alt_bv="Cleared", alt_bn="Cleared {c}. A new window needs a new D0: first pullback after BTC gains 15%+ over 10 days",
   alt_end="Cleared {c}", d24="D0+24",
   acct_tag="Account", acct_title="Since Launch", acct_line="{n} closed · {note}", acct_lbl="Account return ($10,000 start, 2x lev.)",
   k1="Cumulative / Current", k2="Open / Pending", k3="Leverage", lev="2x",
   m_k1="Cumulative", m_k2="Current", m_k3="Open/Pending", m_k4="Leverage", m_acct="Since launch · {n} closed",
   closed="Recently Closed", closed_foot="Full record in the trade log", closed_foot_m="Full record in the trade log. Basis: $10,000 notional account, $2,000 per trade, 2x leverage, by coin-price P&L.",
   issue="Issue {i}", issue_dir="Issue {i} · Long",
   h=["Coin","Entry","Spot","Floating P&L","Trailing stop","Target at entry"], hold="Open Positions", today="Today"),
}
SCALE_LEG = 110   # 涨腿进度条固定刻度尺（天），不随当前天数变化
SCALE_ALT = 30    # 山寨窗口刻度 D0..D0+30

CSS = """
:root{--ink:#1b1b1b;--brown:#6f5b4b;--muted:#8a7a6a;--hair:#e6e0d3;--olive:#8C6C1F;--gray:#8A8477;--blue:#1A6694;
 --green:#1C7A4A;--red:#BE2B2B;--bar-b:#9CC6DE;--bar-g:#E3CD84;--bar-r:#E8A3A7;
 --serif:Georgia,'Times New Roman','Noto Serif CJK SC','Songti SC',serif;
 --mono:'JetBrains Mono','Liberation Mono','Courier New',monospace;
 --sans:-apple-system,'PingFang SC','Noto Sans CJK SC','Microsoft YaHei',Helvetica,Arial,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--sans);color:var(--ink);background:#fff}
.topbar{height:11px;background:#146290}
.kicker{font-family:var(--mono);font-weight:700;color:var(--olive);letter-spacing:.5px}
h1.title{font-weight:500;color:var(--ink)}
.card{border:1px solid var(--hair);position:relative}
.cream{background:#FAF4E8;border-color:#EBDDBD}.purple{background:#F0ECF7;border-color:#DCD5EC}
.tag{font-weight:700}.tag-leg{color:var(--olive)}.tag-alt{color:#5B49A0}.tag-acct{color:var(--blue)}
.h2{font-family:var(--serif);font-weight:700}
.badge{display:inline-block;border-radius:999px;color:#fff;font-weight:700;background:var(--gray);vertical-align:middle}
.badge.sq{border-radius:5px}
.pill{display:inline-block;border-radius:999px;font-weight:700;vertical-align:middle}
.pill.fill{background:var(--olive);color:#fff}.pill.line{border:1px solid #d9cfb9;color:#6f5b4b;background:#fffdf7;font-weight:600}
.sub{color:#333}.sub b{font-family:var(--mono)}
.track{position:relative;border-radius:99px;overflow:hidden;display:flex;background:var(--bar-b)}
.track .s{height:100%}
.tick{position:absolute;width:2px;background:#6f6a60}
.dot{position:absolute;border-radius:50%;background:#fff;border:3px solid #111}
.dot.solid{background:#111}
.lbl-top{position:absolute;font-family:var(--mono);font-weight:700;white-space:nowrap;transform:translateX(-50%)}
.lbl-bot{position:absolute;font-family:var(--mono);white-space:nowrap;color:var(--muted)}
.lbl-cur{position:absolute;font-family:var(--mono);font-weight:700;color:var(--red);white-space:nowrap;transform:translateX(-50%)}
.statbox{background:rgba(255,255,255,.62);border:1px solid #ece4d3}
.purple .statbox{background:rgba(255,255,255,.55);border-color:#e3dcf0}
.sl{color:var(--brown)}.sv{font-family:var(--mono);font-weight:700}.sn{color:var(--muted)}
.acct{background:linear-gradient(115deg,#E6EDF3 0%,#F3F1EA 55%,#FBF3E2 100%);border-color:#e2dccb}
.big{font-family:var(--mono);font-weight:800;color:var(--blue)}
.gbox{background:rgba(255,255,255,.6);border:1px solid #e6e0d3}
.graybox{background:#F9F7F2;border:1px solid var(--hair)}
.pos{color:var(--green)}.neg{color:var(--red)}
.cell{background:#fff;border:1px solid var(--hair)}
.serifsym{font-family:var(--serif);font-weight:700}
.todaytxt{font-family:var(--sans);color:#333}
.side-l{color:var(--blue);font-weight:700}.side-s{color:var(--red);font-weight:700}
"""

def leg_html(L, mobile):
    t = T[L]; D = DATA
    cur_pct = min(D["leg_day"] / SCALE_LEG * 100, 100); hint_pct = 89 / SCALE_LEG * 100
    near = abs(cur_pct - hint_pct) < 9   # 标签防重叠：靠近时当前天数放进度条下方（这里始终放下方，更稳）
    z = dict(  # 尺寸（桌面 / 手机）
        pad=("22px 23px 22px" if not mobile else "22px 22px 20px"), tag=(12.5 if not mobile else 13), h2=(22 if not mobile else 24),
        bd=(12 if not mobile else 13), sub=(12.5 if not mobile else 14), bar=(11 if not mobile else 12),
        lt=(12 if not mobile else 13), lb=(11 if not mobile else 11), sv=(21 if not mobile else 24), sl=(12 if not mobile else 12.5), sn=(11 if not mobile else 11),
        top=(32 if not mobile else 33), h_after=(30 if not mobile else 80))
    return f"""
<div class="card cream" style="padding:{z['pad']};border-radius:{4 if not mobile else 4}px">
  <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
    <span class="tag tag-leg" style="font-size:{z['tag']}px">{t['leg_tag']}</span>
    <span class="h2" style="font-size:{z['h2']}px">{t['leg_title'].format(n=D['leg_day'])}</span>
    <span class="badge sq" style="font-size:{z['bd']}px;padding:{3 if mobile else 2}px {12 if mobile else 9}px">{t['leg_badge']}</span></div>
  <div class="sub" style="font-size:{z['sub']}px;margin-top:10px">{t['leg_sub'].format(s=D['leg_start'],c=D['leg_start_close'],p=D['leg_peak_pct'],note=(D['leg_peak_note'] if L=='zh' else D['leg_peak_note_en']))}</div>
  <div style="position:relative;margin-top:{z['top']}px;height:{z['bar']}px">
    <div class="lbl-top" style="left:{hint_pct:.2f}%;top:-{z['lt']+10}px;font-size:{z['lt']}px">{t['hint']}</div>
    <div class="track" style="height:{z['bar']}px"><div class="s" style="width:100%;background:var(--bar-b)"></div></div>
    <div class="tick" style="left:{hint_pct:.2f}%;top:-3px;height:{z['bar']+6}px"></div>
    {'<div class="dot" style="left:calc(%.2f%% - 6px);top:%dpx;width:12px;height:12px;border-width:2px"></div>' % (cur_pct, (z['bar']-12)//2) if not mobile else ''}
    <div class="lbl-bot" style="left:-2px;top:{z['bar']+8}px;font-size:{z['lb']}px;font-family:var(--sans)">{t['start']}</div>
    {('<div class="lbl-cur" style="left:%.2f%%;top:%dpx;font-size:%dpx">%s</div><div class="dot solid" style="left:calc(%.2f%% - 6px);top:%dpx;width:12px;height:12px;border:2px solid #fff"></div>' % (cur_pct, z['bar']+24, z['lt']+1, t['cur'].format(n=D['leg_day']), cur_pct, z['bar']+46)) if mobile else ('<div class="lbl-cur" style="left:%.2f%%;top:%dpx;font-size:%dpx">%s</div>' % (cur_pct, z['bar']+8, z['lb'], t['cur'].format(n=D['leg_day'])))}
  </div>
  <div style="height:{z['h_after']}px"></div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
    <div class="statbox" style="padding:12px 18px;line-height:1.3"><div class="sl" style="font-size:{z['sl']}px">{t['st1'].format(d=D['leg_dd_date'])}</div>
      <div class="sv" style="font-size:{z['sv']}px;margin:3px 0 5px">{D['leg_dd']}</div><div class="sn" style="font-size:{z['sn']}px">{t['st1n']}</div></div>
    <div class="statbox" style="padding:12px 18px;line-height:1.3"><div class="sl" style="font-size:{z['sl']}px">{t['st2']}</div>
      <div class="sv" style="font-size:{z['sv']}px;margin:3px 0 5px;font-family:var(--sans)">{t['st2v'].format(n=D['leg_past'])}</div><div class="sn" style="font-size:{z['sn']}px">{t['st2n']}</div></div>
  </div></div>"""

def alt_html(L, mobile):
    t = T[L]; D = DATA
    if mobile and L == "zh":   # 29期手机版标准文案（比桌面版稍完整）
        t = dict(t, alt_sub="窗口起点 D0=<b>{d0}</b>（BTC收{c}）· 峰值涨幅最高三个：{top}", alt_a="这条线只验证时机，不验证选币",
                 alt_an="634币-窗口测过：机械选币8个变体没跑赢等权篮子（+9.0% vs +12.9%）")
    p24 = 24 / SCALE_ALT * 100; p28 = 28 / SCALE_ALT * 100
    z = dict(pad=("22px 23px 22px" if not mobile else "22px 22px 20px"), tag=(12.5 if not mobile else 13), h2=(22 if not mobile else 24),
             bd=(12 if not mobile else 13), sub=(12.5 if not mobile else 14), bar=(11 if not mobile else 12), lt=(12 if not mobile else 13),
             lb=(11 if not mobile else 11), sv=(21 if not mobile else 24), sl=(12 if not mobile else 12.5), sn=(11 if not mobile else 11),
             top=(32 if not mobile else 62), h_after=(30 if not mobile else 77))
    mob_labels = ""
    if mobile:  # 手机版：区域名称标签按区域中心点精确定位
        mob_labels = (f'<div class="lbl-bot" style="left:{p24/2:.2f}%;top:-20px;transform:translateX(-50%);font-family:var(--sans);font-size:{z["lb"]}px">建仓窗</div>'
                      f'<div class="lbl-bot" style="left:{(p24+(100-p24)/2):.2f}%;top:-20px;transform:translateX(-50%);font-family:var(--sans);font-size:{z["lb"]}px">只减不加</div>') if L == "zh" else (
                      f'<div class="lbl-bot" style="left:{p24/2:.2f}%;top:-20px;transform:translateX(-50%);font-family:var(--sans);font-size:{z["lb"]}px">Build window</div>'
                      f'<div class="lbl-bot" style="left:{(p24+(100-p24)/2):.2f}%;top:-20px;transform:translateX(-50%);font-family:var(--sans);font-size:{z["lb"]}px">Trim only</div>')
    cur_lbl = (f'D0+{D["alt_day"]} 天' if L == "zh" else f'D0+{D["alt_day"]}')
    return f"""
<div class="card purple" style="padding:{z['pad']};border-radius:4px">
  <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
    <span class="tag tag-alt" style="font-size:{z['tag']}px">{t['alt_tag']}</span>
    <span class="h2" style="font-size:{z['h2']}px">{t['alt_title'].format(n=D['alt_day'])}</span>
    <span class="badge sq" style="font-size:{z['bd']}px;padding:{3 if mobile else 2}px {12 if mobile else 9}px">{t['alt_badge']}</span></div>
  <div class="sub" style="font-size:{z['sub']}px;margin-top:10px">{t['alt_sub'].format(d0=D['alt_d0'],c=D['alt_d0_close'],top=(D['alt_top3'] if L=='zh' else D['alt_top3_en']))}</div>
  <div style="position:relative;margin-top:{z['top']}px;height:{z['bar']}px">
    <div class="lbl-top" style="left:{p24:.2f}%;top:-{z['lt']+(28 if mobile else 10)}px;font-size:{z['lt']}px">{t['d24']}</div>
    {mob_labels}
    <div class="track" style="height:{z['bar']}px;background:none"><div class="s" style="width:{p24:.2f}%;background:var(--bar-b)"></div><div class="s" style="width:{p28-p24:.2f}%;background:var(--bar-g)"></div><div class="s" style="width:{100-p28:.2f}%;background:var(--bar-r)"></div></div>
    <div class="tick" style="left:{p24:.2f}%;top:-3px;height:{z['bar']+6}px"></div><div class="tick" style="left:{p28:.2f}%;top:-3px;height:{z['bar']+6}px"></div>
    {'<div class="dot" style="left:calc(100%% - 12px);top:%dpx;width:12px;height:12px;border-width:2px"></div>' % ((z['bar']-12)//2) if not mobile else ''}
    <div class="lbl-bot" style="left:-2px;top:{z['bar']+8}px;font-size:{z['lb']}px">{D['alt_d0']}</div>
    <div class="lbl-bot" style="right:0;top:{z['bar']+8}px;font-size:{z['lb']}px">{t['alt_end'].format(c=D['alt_cleared'])}</div>
    {('<div style="position:absolute;right:0;top:%dpx;display:flex;flex-direction:column;align-items:center"><span class="lbl-cur" style="position:static;transform:none;font-size:%dpx">%s</span><div class="dot solid" style="position:static;margin-top:7px;width:12px;height:12px;border:2px solid #fff"></div></div>' % (z['bar']+24, z['lt']+1, cur_lbl)) if mobile else ''}
  </div>
  <div style="height:{z['h_after']}px"></div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
    <div class="statbox" style="padding:12px 18px;line-height:1.3"><div class="sl" style="font-size:{z['sl']}px">{t['alt_a']}</div>
      <div style="font-size:{z['sv']}px;font-weight:700;margin:3px 0 5px">{t['alt_av']}</div><div class="sn" style="font-size:{z['sn']}px">{t['alt_an']}</div></div>
    <div class="statbox" style="padding:12px 18px;line-height:1.3"><div class="sl" style="font-size:{z['sl']}px">{t['alt_b']}</div>
      <div style="font-size:{z['sv']}px;font-weight:700;margin:3px 0 5px">{t['alt_bv']}</div><div class="sn" style="font-size:{z['sn']}px">{t['alt_bn'].format(c=D['alt_cleared'])}</div></div>
  </div></div>"""

def acct_desktop(L):
    t = T[L]; D = DATA
    return f"""
<div class="card acct" style="padding:20px 30px 22px;border-radius:4px">
  <div style="display:flex;align-items:baseline;gap:12px"><span class="tag tag-acct" style="font-size:12px">{t['acct_tag']}</span><span style="font-size:23px;font-weight:700">{t['acct_title']}</span></div>
  <div style="font-size:15px;color:#333;margin-top:6px">{t['acct_line'].format(n=D['acct_closed'],note=(D['acct_note'] if L=='zh' else D['acct_note_en']))}</div>
  <div style="display:flex;align-items:baseline;gap:10px;margin-top:14px"><span class="big" style="font-size:38px">{D['acct_pct']}</span><span style="font-size:13px;color:#333">{t['acct_lbl']}</span></div>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:11px;margin-top:24px">
    <div class="gbox" style="padding:10px 14px;line-height:1.3"><div class="sl" style="font-size:12px">{t['k1']}</div><div style="margin-top:6px"><span class="sv" style="font-size:21px">{D['acct_cum']}</span> <span class="sv" style="font-size:15px;color:var(--muted)">/ {D['acct_cur']}</span></div></div>
    <div class="gbox" style="padding:10px 14px;line-height:1.3"><div class="sl" style="font-size:12px">{t['k2']}</div><div class="sv" style="font-size:21px;margin-top:6px">{D['acct_open']} / {D['acct_pending']}</div></div>
    <div class="gbox" style="padding:10px 14px;line-height:1.3"><div class="sl" style="font-size:12px">{t['k3']}</div><div style="font-size:21px;font-weight:700;margin-top:6px">{t['lev']}</div></div>
  </div></div>"""

def closed_desktop(L):
    t = T[L]; items = ""
    for sym, iss, pct, usd in CLOSED[:6]:
        cls = "pos" if pct.startswith("+") else "neg"
        items += f"""<div class="cell" style="padding:8px 14px 7px;line-height:1.25"><div><span class="serifsym" style="font-size:16px">{sym}</span> <span style="font-size:11px;color:var(--muted)">{t['issue'].format(i=iss)}</span></div>
          <div class="sv {cls}" style="font-size:16px;margin-top:3px">{pct}</div><div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:1px">{usd}</div></div>"""
    return f"""
<div class="card graybox" style="padding:19px 23px 20px;border-radius:4px">
  <div style="font-size:12.5px;font-weight:700;color:var(--brown)">{t['closed']}</div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:9px">{items}</div>
  <div style="font-size:12px;color:var(--brown);margin-top:12px">{t['closed_foot']}</div></div>"""

def side_html(r, L):
    cls = "side-s" if r["side"] == "空" else "side-l"
    s = r["side"] if L == "zh" else ("Short" if r["side"] == "空" else "Long")
    return f'<span class="{cls}" style="font-size:14px">{s}</span>'

def table_desktop(L):
    t = T[L]; rows = ""
    for r in ROWS:
        pnl = r["pnl_zh"] if L == "zh" else r["pnl_en"]; psub = r["pnl_sub_zh"] if L == "zh" else r["pnl_sub_en"]
        state = r["state"] if L == "zh" else r["state_en"]; size = r["size"] if L == "zh" else r["size_en"]
        tf = r["tf_zh"] if L == "zh" else r["tf_en"]
        pcol = {"gray": "var(--muted)"}.get(r["pnl_cls"], "var(--green)")
        stopcol = "var(--olive)" if r["stop_moved"] else "#111"
        rows += f"""<tr>
  <td style="padding:18px 8px"><span class="serifsym" style="font-size:25px">{r['sym']}</span> {side_html(r,L)} <span style="font-size:11px;color:var(--muted)">{tf}</span>
     <span class="pill fill" style="font-size:12.5px;padding:3px 12px;margin-left:4px">{state}</span> <span class="pill line" style="font-size:12.5px;padding:3px 11px">{size}</span></td>
  <td style="padding:18px 8px"><span class="sv" style="font-size:26px">{r['entry']}</span></td>
  <td style="padding:18px 8px"><span style="font-family:var(--mono);font-size:16px;color:var(--brown)">{r['price']}</span></td>
  <td style="padding:18px 8px"><span style="font-family:var(--mono);font-size:16px;color:{pcol}">{pnl}</span><div style="font-family:var(--mono);font-size:12.5px;color:var(--muted);margin-top:3px">{psub}</div></td>
  <td style="padding:18px 8px"><span style="font-family:var(--mono);font-size:26px;color:{stopcol}">{r['stop']}</span><div style="font-family:var(--mono);font-size:12.5px;color:var(--olive);margin-top:3px">{r['stop_sub']}</div></td>
  <td style="padding:18px 8px"><span style="font-family:var(--mono);font-size:26px">{r['target']}</span><div style="font-family:var(--mono);font-size:12.5px;color:var(--muted);margin-top:3px">{r['target_sub']}</div></td></tr>"""
    th = "".join(f'<th style="text-align:left;padding:10px 8px;font-size:13px;color:var(--brown);font-weight:700;border-bottom:1.5px solid #d8d0c0">{h}</th>' for h in t["h"])
    note = DATA["note_zh"] if L == "zh" else DATA["note_en"]
    return f"""<table style="width:100%;border-collapse:collapse;table-layout:fixed;margin-top:12px"><colgroup>{'<col style="width:16.666%">'*6}</colgroup>
<thead><tr>{th}</tr></thead><tbody style="border-bottom:1px solid var(--hair)">{rows}</tbody></table>
<div style="font-size:14.5px;color:var(--brown);margin:14px 8px 0">{note}</div>"""

def today_box(L, mobile):
    t = T[L]; D = DATA
    badge = D["badge_today_zh"] if L == "zh" else D["badge_today_en"]; txt = D["today_zh"] if L == "zh" else D["today_en"]
    fs = 16
    if mobile:
        return f"""<div class="card graybox" style="padding:22px 26px;margin-top:26px;border-radius:4px"><div style="display:flex;align-items:center;gap:12px"><span style="font-size:24px;font-weight:800">{t['today']}</span><span class="badge" style="font-size:14.5px;padding:3px 14px">{badge}</span></div>
          <div class="todaytxt" style="font-size:{fs}px;line-height:1.65;margin-top:14px">{txt}</div></div>"""
    return f"""<div class="card graybox" style="padding:22px 28px;margin-top:26px;display:flex;gap:22px;align-items:flex-start;border-radius:4px">
      <div style="display:flex;align-items:center;gap:12px;white-space:nowrap;padding-top:2px"><span style="font-size:19px;font-weight:700">{t['today']}</span><span class="badge" style="font-size:12px;padding:3px 10px">{badge}</span></div>
      <div class="todaytxt" style="font-size:{fs}px;line-height:1.8;flex:1">{txt}</div></div>"""

def footer_html(L, mobile):
    legal = FOOTER_ZH if L == "zh" else FOOTER_EN
    if mobile:
        return f"""<div style="border-top:1px solid #d8d0c0;margin-top:30px;padding-top:22px"><div style="font-size:15px;color:var(--brown);line-height:1.7">{legal}</div>
          <div class="serifsym" style="font-size:24px;margin-top:20px">{SIGNATURE if L=="zh" else SIGNATURE_EN}</div><div style="font-size:15px;color:var(--muted);margin-top:2px">{HANDLE}</div></div>"""
    return f"""<div style="border-top:1px solid #d8d0c0;margin-top:34px;padding-top:24px;display:flex;justify-content:space-between;align-items:flex-end;gap:30px">
      <div style="font-size:13px;color:var(--brown);flex:1 1 auto;min-width:0;line-height:1.7">{legal}</div>
      <div style="text-align:right;white-space:nowrap"><div class="serifsym" style="font-size:22px">{SIGNATURE if L=="zh" else SIGNATURE_EN}</div><div style="font-size:12.5px;color:var(--muted)">{HANDLE}</div></div></div>"""

def hold_cards_mobile(L):
    t = T[L]; out = ""
    for r in ROWS:
        pnl = r["pnl_zh"] if L == "zh" else r["pnl_en"]; psub = r["pnl_sub_zh"] if L == "zh" else r["pnl_sub_en"]
        state = r["state"] if L == "zh" else r["state_en"]; size = r["size"] if L == "zh" else r["size_en"]; tf = r["tf_zh"] if L == "zh" else r["tf_en"]
        pcol = {"gray": "var(--muted)"}.get(r["pnl_cls"], "var(--green)")
        stopcol = "var(--olive)" if r["stop_moved"] else "#111"
        cols = [(t["h"][1], f'<span class="sv" style="font-size:27px">{r["entry"]}</span>', ""),
                (t["h"][2], f'<span class="sv" style="font-size:27px;color:var(--brown)">{r["price"]}</span>', ""),
                (t["h"][3], f'<span class="sv" style="font-size:27px;color:{pcol}">{pnl}</span>', psub),
                (t["h"][4], f'<span class="sv" style="font-size:27px;color:{stopcol}">{r["stop"]}</span>', r["stop_sub"]),
                (t["h"][5], f'<span class="sv" style="font-size:27px">{r["target"]}</span>', r["target_sub"])]
        cells = "".join(f'<div><div style="font-size:15.5px;color:var(--brown)">{h}</div><div style="margin-top:6px;white-space:nowrap">{v}</div><div style="font-family:var(--mono);font-size:13.5px;color:var(--olive);margin-top:3px;min-height:18px">{s}</div></div>' for h, v, s in cols)
        out += f"""<div class="card graybox" style="padding:16px 24px 14px;margin-top:16px;border-radius:4px">
          <div style="display:flex;align-items:center;gap:10px"><span class="serifsym" style="font-size:30px">{r['sym']}</span>{side_html(r,L)}<span style="font-size:14px;color:var(--muted)">{tf}</span>
          <span class="pill fill" style="font-size:13.5px;padding:3px 13px">{state}</span><span class="pill line" style="font-size:13.5px;padding:3px 12px">{size}</span></div>
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:10px">{cells}</div></div>"""
    return out

def closed_mobile(L):
    t = T[L]; items = ""
    for sym, iss, pct, usd in CLOSED[:6]:
        cls = "pos" if pct.startswith("+") else "neg"
        items += f"""<div class="graybox" style="padding:10px 8px 10px 9px"><div style="font-size:23px;font-weight:700">{sym}</div><div style="font-size:13px;color:var(--brown);margin-top:2px;white-space:nowrap">{t['issue_dir'].format(i=iss)}</div>
          <div class="sv {cls}" style="font-size:28px;margin-top:9px;white-space:nowrap">{pct}</div><div style="font-family:var(--mono);font-size:13px;color:var(--muted);margin-top:4px;white-space:nowrap">{usd}</div></div>"""
    return f"""<div style="font-size:26px;font-weight:800;margin-top:34px">{t['closed']}</div>
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:14px">{items}</div>
      <div style="font-size:15px;color:var(--brown);margin-top:14px">{t['closed_foot_m']}</div>"""

def acct_mobile(L):
    t = T[L]; D = DATA
    boxes = [(t["m_k1"], D["acct_cum"], False), (t["m_k2"], D["acct_cur"], False), (t["m_k3"], f"{D['acct_open']} / {D['acct_pending']}", False), (t["m_k4"], t["lev"], True)]
    cells = "".join(f'<div class="gbox" style="padding:12px 14px;background:rgba(255,255,255,.85)"><div class="sl" style="font-size:15px">{k}</div><div class="{"" if sans else "sv"}" style="font-size:25px;font-weight:800;margin-top:5px;{"" if sans else "font-family:var(--mono)"};white-space:nowrap">{v}</div></div>' for k, v, sans in boxes)
    return f"""<div class="acct" style="border-left:7px solid #146290;padding:20px 22px 24px;margin-top:22px">
      <div style="font-size:15px;color:#333">{t['m_acct'].format(n=D['acct_closed'])}</div><div class="big" style="font-size:34px;margin-top:4px">{D['acct_pct']}</div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0;margin-top:14px;border:1px solid #e0d9c8">{cells}</div></div>"""

def build(L, dev):
    mobile = dev == "mobile"; t = T[L]; D = DATA
    width = 1080 if mobile else 2400
    kicker = t["kicker"].format(n=D["issue"], d=D["date"], dev=t["dev_m"] if mobile else t["dev_d"])
    if mobile:
        wrap = 30
        body = f"""<div style="padding:22px {wrap}px 44px">
  <div class="kicker" style="font-size:15px">{kicker}</div><h1 class="title" style="font-size:38px;margin-top:6px">{t['title']}</h1>
  <div style="margin-top:20px">{leg_html(L,True)}</div><div style="margin-top:22px">{alt_html(L,True)}</div>
  {acct_mobile(L)}
  <div style="font-size:26px;font-weight:800;margin-top:34px">{t['hold']}</div>{hold_cards_mobile(L)}
  <div style="font-size:14.5px;color:var(--brown);margin:14px 6px 0">{D['note_zh'] if L=='zh' else D['note_en']}</div>
  {today_box(L,True)}{closed_mobile(L)}{footer_html(L,True)}</div>"""
    else:
        body = f"""<div style="padding:34px 68px 40px">
  <div class="kicker" style="font-size:16.5px">{kicker}</div><h1 class="title" style="font-size:52px;margin-top:12px">{t['title']}</h1>
  <div style="display:grid;grid-template-columns:1.35fr 1fr;gap:20px;margin-top:30px;align-items:start">
    <div style="display:flex;flex-direction:column;gap:20px">{leg_html(L,False)}{alt_html(L,False)}</div>
    <div style="display:flex;flex-direction:column;gap:20px">{acct_desktop(L)}<div>{closed_desktop(L)}</div></div></div>
  {table_desktop(L)}{today_box(L,False)}{footer_html(L,False)}</div>"""
    return f"""<!doctype html><html lang="{'zh-CN' if L=='zh' else 'en'}"><head><meta charset="utf-8"><style>{CSS}body{{width:{width}px}}</style></head>
<body><div class="topbar"></div>{body}</body></html>"""

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for L in ("zh", "en"):
        for dev in ("desktop", "mobile"):
            open(os.path.join(here, f"poster_{L}_{dev}.html"), "w", encoding="utf-8").write(build(L, dev)); print("wrote", L, dev)
