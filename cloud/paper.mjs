// 纸面交易机会卡：监控提醒触发时，主机按这里算好的卡片给出“进场价、止损、止盈、仓位”，并决定这条提醒算不算机会。
// 纯函数，不碰网络和存储。判断只用日线级别的快照（指标 / 关键位 / 做空筛选 / L1L2 读数），15 分钟数据只负责触发，
// 不参与“是不是机会”的判断。规则照搬《操作规格》v6.5：做多顺势买回踩不抄底，做空走做空筛选的三道门，
// 止损 0.5×ATR14（日线下限 2%）按日线收盘，目标机械 4R，2R 止损到进场价，3R 止损跟 MA20。
import {TRADABLE,fmtPrice,fmtPct,whyNoCandidate} from './order.mjs';

export const PAPER={account_usd:10000,leverage:2,base_usd:2000,max_notional_usd:20000,
  max_same_side_major:3,major:['BTC','ETH','SOL','XRP','BNB'],max_hold_days:20,
  max_dist_pct:6,touched_gap_days:15,touched_mult:1/3,fee_pct:0.1};
const round=(value,digits=6)=>Number(Number(value).toPrecision(digits));
const num=value=>typeof value==='number' && Number.isFinite(value)?value:null;
export const cardKey=(sym,kind,value)=>`${sym}|${kind}|${Number(value).toPrecision(6)}`;

// 宏观侧：L1 折扣（程序已判定）、L2 周期状态（从“四行”文字里读状态名）。读不到的闸门在卡片里明说“未核对”。
export function macroView(summary) {
  const l1l4=summary?.['L1L4']||{};
  const discount=l1l4['L1折扣判定']||null;
  const l2Line=String(l1l4['四行']?.L2||'');
  const state=(l2Line.match(/^L2：([^｜]+)/)||[])[1]||null;
  return {
    discount_complete:Boolean(discount && discount['数据完整']===true),
    l1_mult:num(discount?.['新开仓倍数'])??1,
    l2_state:state,
    l2_top_risk:Boolean(state && state.includes('顶部风险')),   // 多头只减不加
    l2_bear:Boolean(state && state.startsWith('熊市')),         // 新开多单 ×0.5
    l2_known:Boolean(state)
  };
}

const UNCHECKED=['宏观闸门：涨腿89天线、MA20+7日跌幅、48小时二元事件（程序读不到，亮灯则这单作废，需你核对）',
  '事件窗口：非农/FOMC/CPI/监管投票前后不挂近价单（程序未核对）'];

function stopTarget(entry,atrPct,long) {
  const stopPct=Math.max(0.5*atrPct,2)/100;
  const risk=entry*stopPct;
  return long?{stop:round(entry-risk),risk,target:round(entry+4*risk),r2:round(entry+2*risk),r3:round(entry+3*risk),stopPct:stopPct*100}
    :{stop:round(entry+risk),risk,target:round(entry-4*risk),r2:round(entry-2*risk),r3:round(entry-3*risk),stopPct:stopPct*100};
}

function sizing(card,direction,levelMult,macro,long) {
  const parts=[['方向',direction],['位置',levelMult],['L1折扣',macro.l1_mult]];
  if(long && macro.l2_bear) parts.push(['L2熊市',0.5]);
  const size=parts.reduce((product,[,value])=>product*value,1);
  card.size=round(size,4);
  card.size_parts=parts.map(([name,value])=>`${name}×${round(value,3)}`).join(' ');
  card.notional_usd=Math.round(PAPER.base_usd*size);
  card.margin_usd=Math.round(card.notional_usd/PAPER.leverage);
  card.risk_usd=Math.round(card.notional_usd*card.stop_pct)/100;
  card.risk_pct=Math.round(card.risk_usd/PAPER.account_usd*10000)/100;
}

function base(sym,side,level,m) {
  return {key:cardKey(sym,side==='多'?'down':'up',level.mid),sym,side,entry:level.mid,
    level:{segs:level.segs??null,days:level.days??null,age:level.age??null,gap:level.gap??null,dist:level.dist??null},
    atr_d_pct:num(m.atr_d_pct),qualified:false,blocks:[],passes:[],unchecked:UNCHECKED};
}
function finish(card,long,m,macro,levelMult,direction) {
  const {stop,target,r2,r3,stopPct}=stopTarget(card.entry,m.atr_d_pct,long);
  Object.assign(card,{stop,target,r2,r3,stop_pct:round(stopPct,4),target_pct:round(stopPct*4,4)});
  sizing(card,direction,levelMult,macro,long);
  card.qualified=card.blocks.length===0;
}

function longCard(sym,level,m,macro) {
  const card=base(sym,'多',level,m);
  if(num(m.atr_d_pct)===null || num(m.price)===null || num(m.ma200)===null) {
    card.blocks.push('日线数据不足（ATR / MA200 缺失）');
    return card;
  }
  if(m['慢档可用']===false) card.blocks.push('日线跨度不足，慢档不可用');
  if(m.price<m.ma200) card.blocks.push(`价格在MA200下方（${fmtPct(m.dev200??(m.price/m.ma200-1)*100)}）：逆势，不抄底`);
  else card.passes.push(`价格在MA200上方 ${fmtPct(m.dev200??(m.price/m.ma200-1)*100)}（顺势）`);
  if(level.mid<m.ma200) card.blocks.push(`支撑位 ${fmtPrice(level.mid)} 在MA200 ${fmtPrice(m.ma200)} 下方`);
  if(level.dist!==null && Math.abs(level.dist)>PAPER.max_dist_pct) card.blocks.push(`分析时距现价 ${fmtPct(level.dist)}，超过挂单上限 ${PAPER.max_dist_pct}%`);
  if(m['阶段']==='数据不足') card.blocks.push('阶段数据不足');
  if(macro.l2_top_risk) card.blocks.push(`L2=${macro.l2_state}：多头只减不加`);
  if(!macro.discount_complete) card.blocks.push('L1宏观读数不全，按规则不开新单');
  if(!card.blocks.length) card.passes.push(`日线支撑 ${level.segs}段接触`+(level.days?`、跨${level.days}天`:''));
  const touched=level.gap!==undefined && level.gap!==null && level.gap<=PAPER.touched_gap_days;
  if(touched) card.passes.push(`15天内碰过：仓位 ×1/3`);
  if(macro.l1_mult<1) card.passes.push(`L1折扣：仓位 ×${macro.l1_mult}`);
  if(macro.l2_bear) card.passes.push('L2熊市：新开多单 ×0.5');
  finish(card,true,m,macro,touched?PAPER.touched_mult:1,1);
  return card;
}

function shortCard(sym,level,m,macro,screen) {
  const card=base(sym,'空',level,m);
  if(num(m.atr_d_pct)===null) {
    card.blocks.push('日线数据不足（ATR 缺失）');
    return card;
  }
  const candidates=Array.isArray(screen?.['候选'])?screen['候选']:[];
  const match=candidates.find(item=>Math.abs(item['进场']/level.mid-1)<0.0005);
  if(!match) {
    card.blocks.push(whyNoCandidate(sym,screen).replace(/^\$\w+：/,'做空筛选：'));
  } else if(screen['费率否决']) {
    card.blocks.push(String(screen['门③路径']||'资金费率条件否决'));
  } else if(typeof screen['门③需要人工核实']==='string' && screen['门③需要人工核实']) {
    card.blocks.push(screen['门③需要人工核实']);
  } else {
    card.passes.push(`做空筛选过三道门（${screen['门③路径']||'MA200下方不受门③限制'}）`,`日线压力位 ${level.segs}段接触`);
  }
  if(!macro.discount_complete) card.blocks.push('L1宏观读数不全，按规则不开新单');
  const direction=num(match?.['方向倍数'])??0.5;
  const touched=match?Boolean(match['15天内碰过']):(level.gap??999)<=PAPER.touched_gap_days;
  if(touched) card.passes.push('15天内碰过：仓位 ×1/3');
  if(macro.l1_mult<1) card.passes.push(`L1折扣：仓位 ×${macro.l1_mult}`);
  card.passes.push('做空方向倍数 0.5');
  finish(card,false,m,macro,touched?PAPER.touched_mult:1,direction);
  return card;
}

// 返回 {cards: {规则key: 卡片}, macro}
export function buildCards(snapshot) {
  const summary=snapshot?.summary||{},metrics=summary['指标']||{},screens=summary['做空筛选']||{};
  const macro=macroView(summary),cards={};
  const facts=snapshot?.poster_facts;   // 涨腿起点是规则状态，天数/回撤由每日取数按日线收盘算好；主机每天按起点重算天数
  macro.leg=facts?.ok===true && facts.leg?.start && num(facts.leg.dd)!==null
    ?{ok:true,start:facts.leg.start,day:facts.leg.day,dd:facts.leg.dd,peak_pct:num(facts.leg.peak_pct)}:{ok:false};
  for(const sym of TRADABLE) {
    const m=metrics[sym];
    if(!m) continue;
    for(const side of ['above','below']) {
      const level=(Array.isArray(m['d1_'+side])?m['d1_'+side]:[])
        .filter(item=>num(item?.mid)!==null && num(item?.dist)!==null && (side==='above'?item.dist>0:item.dist<0))
        .sort((a,b)=>Math.abs(a.dist)-Math.abs(b.dist))[0];
      if(!level || m['慢档可用']===false) continue;
      const card=side==='below'?longCard(sym,level,m,macro):shortCard(sym,level,m,macro,screens[sym]);
      cards[card.key]=card;
    }
  }
  return {cards,macro};
}
