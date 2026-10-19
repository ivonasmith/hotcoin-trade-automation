import test from 'node:test';
import assert from 'node:assert/strict';
import {eligible,beijingDay,dueAt} from '../calendar.mjs';
test('周末不因为中国调休而恢复发帖',()=>{
  assert.equal(eligible('2026-10-10').ok,false);
  assert.equal(eligible('2026-02-28').ok,false);
});
test('中国假期和美国补休日都跳过',()=>{
  for (const d of ['2026-10-01','2026-10-07','2026-02-23','2026-07-03','2026-11-26'])
    assert.equal(eligible(d).ok,false,d);
});
test('普通工作日和北京时间排期',()=>{
  assert.equal(eligible('2026-10-19').ok,true);
  assert.equal(dueAt('2026-10-19','zh'),Date.parse('2026-10-19T12:00:00Z'));
  assert.equal(dueAt('2026-10-19','en'),Date.parse('2026-10-19T13:00:00Z'));
  assert.equal(beijingDay(Date.parse('2026-10-18T16:00:00Z')),'2026-10-19');
});
test('未知年份和非法日期不能误发',()=>{
  for (const d of ['2027-01-04','2026-02-30','2026-13-01']) assert.equal(eligible(d).ok,false);
});
