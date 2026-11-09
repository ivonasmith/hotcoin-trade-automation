import test from 'node:test';
import assert from 'node:assert/strict';
import {boardText} from '../cloud/board.mjs';

test('board text lists movers, notices and gaps; empty when no board',()=>{
  const text=boardText({'市场异动':{'异动':[{'标的':'SOL','涨跌24h_pct':9.5,'成交额_美元':3e9,'成交额倍数':3,'资金费率_pct':0.06,'标签':['大涨','放量']}],
    '币安公告48h':['上线 ABC'],'缺口':['资金费率取不到']}});
  assert.match(text,/SOL：24h \+9\.50%，成交额30\.0亿美元（前7日日均的3倍）/);
  assert.match(text,/上线 ABC/);
  assert.match(text,/未取到（不得当作没有）：资金费率取不到/);
  assert.equal(boardText({}),'');
});
