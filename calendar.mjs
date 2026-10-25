// 2026 calendar verified against CN government notice and US OPM on 2026-10-19.
// Unknown years fail closed. US means federal observed holidays, not NYSE calendar.
export const CN_RANGES = [
  ['01-01','01-03'], ['02-15','02-23'], ['04-04','04-06'],
  ['05-01','05-05'], ['06-19','06-21'], ['09-25','09-27'], ['10-01','10-07'],
];
export const US_DAYS = ['01-01','01-19','02-16','05-25','06-19','07-03',
  '09-07','10-12','11-11','11-26','12-25'];
export function eligible(day) {
  if (!/^2026-\d{2}-\d{2}$/.test(day)) return {ok:false, reason:'日历年份未核验'};
  const date = new Date(day+'T12:00:00Z');
  if (!Number.isFinite(date.valueOf()) || date.toISOString().slice(0,10)!==day)
    return {ok:false, reason:'无效日期'};
  if ([0,6].includes(date.getUTCDay())) return {ok:false, reason:'周末'};
  const md=day.slice(5);
  if (CN_RANGES.some(([a,b])=>md>=a&&md<=b)) return {ok:false, reason:'中国放假日'};
  if (US_DAYS.includes(md)) return {ok:false, reason:'美国联邦节假日'};
  return {ok:true, reason:'发布日'};
}
export function beijingDay(now=Date.now()) {
  return new Date(now+8*3600000).toISOString().slice(0,10);
}
export function dueAt(day,lang) {
  if (!['zh','en'].includes(lang)) throw Error('语言必须为zh/en');
  if (!eligible(day).ok) throw Error('非发布日');
  return Date.parse(`${day}T${lang==='zh'?'20':'21'}:00:00+08:00`);
}

// 周日「下周清单」：只在周日发，中英文都是北京时间 20:00。不看节假日（周日本来就不是工作日发布日）；未核验的年份同样拒绝。
export const WEEKLY_TIME='20:00';
export function weeklyDay(day) {
  if (!/^2026-\d{2}-\d{2}$/.test(day)) return {ok:false, reason:'日历年份未核验'};
  const date = new Date(day+'T12:00:00Z');
  if (!Number.isFinite(date.valueOf()) || date.toISOString().slice(0,10)!==day) return {ok:false, reason:'无效日期'};
  return date.getUTCDay()===0 ? {ok:true, reason:'周日清单日'} : {ok:false, reason:'不是周日'};
}
export function weeklyDueAt(day,lang) {
  if (!['zh','en'].includes(lang)) throw Error('语言必须为zh/en');
  if (!weeklyDay(day).ok) throw Error('不是周日清单日');
  return Date.parse(`${day}T${WEEKLY_TIME}:00+08:00`);
}
