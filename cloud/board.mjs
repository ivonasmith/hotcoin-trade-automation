// Turns the program-made market-movers board (summary['市场异动'], built by pipeline/hot_scan.py)
// into plain text for the drafting prompt. Numbers only; the board never explains causes.
const usd=value=>{
  const n=Number(value);
  if(!Number.isFinite(n)) return '—';
  return n>=1e8?(n/1e8).toFixed(1)+'亿':n>=1e4?(n/1e4).toFixed(0)+'万':String(Math.round(n));
};
const pct=value=>value!==null && value!==undefined && Number.isFinite(Number(value))
  ?(Number(value)>0?'+':'')+Number(value).toFixed(2)+'%':'—';

export function boardText(summary) {
  const board=summary?.['市场异动'];
  if(!board || typeof board!=='object') return '';
  const lines=['程序异动榜（程序统计，没有新闻或叙事来源：只能陈述下列数字，不得推测原因）：'];
  const movers=Array.isArray(board['异动'])?board['异动']:[];
  if(movers.length) {
    for(const m of movers)
      lines.push(`- ${m['标的']}：24h ${pct(m['涨跌24h_pct'])}，成交额${usd(m['成交额_美元'])}美元`+
        `${m['成交额倍数']!=null?`（前7日日均的${m['成交额倍数']}倍）`:''}`+
        `${m['资金费率_pct']!=null?`，单期资金费率${pct(m['资金费率_pct'])}`:''}｜${(m['标签']||[]).join('、')}`);
  } else lines.push('- 今天币池内没有达到异动口径的币。');
  const notices=Array.isArray(board['币安公告48h'])?board['币安公告48h']:[];
  if(notices.length) lines.push('币安近48小时公告：'+notices.join('；'));
  const trending=Array.isArray(board['CoinGecko热搜'])?board['CoinGecko热搜']:[];
  if(trending.length) lines.push('CoinGecko热搜：'+trending.map(c=>c['币']).filter(Boolean).join('、'));
  const gaps=Array.isArray(board['缺口'])?board['缺口']:[];
  if(gaps.length) lines.push('未取到（不得当作没有）：'+gaps.join('；'));
  return lines.join('\n');
}
