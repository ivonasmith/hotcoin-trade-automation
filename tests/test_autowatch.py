import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
import autowatch  # noqa: E402
import hotcoin_host as host  # noqa: E402
import monitor  # noqa: E402

BJ = timezone(timedelta(hours=8))
BANNED = ["建议", "应该", "应当", "值得", "买入", "卖出", "抄底", "追高", "加仓", "减仓", "入场", "可以买", "可以卖"]


def bj(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=BJ)


def ms(dt):
    return int(dt.timestamp() * 1000)


def kl(open_dt, o, h, l, c, minutes=15):
    start = ms(open_dt)
    return [start, str(o), str(h), str(l), str(c), "0", start + minutes * 60000 - 1]


class Feed:
    """假的币安接口：按 (币, 周期) 返回事先放好的 K 线；没放的币当作合约不存在。"""

    def __init__(self):
        self.rows, self.calls = {}, []

    def set(self, sym, interval, rows):
        self.rows[(sym, interval)] = rows

    def __call__(self, url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        sym, interval = query["symbol"][0].replace("USDT", ""), query["interval"][0]
        self.calls.append((sym, interval))
        if (sym, interval) not in self.rows:
            body = _Body(b'{"code":-1121,"msg":"Invalid symbol."}')
            raise urllib.error.HTTPError(url, 400, "bad", {}, body)
        return self.rows[(sym, interval)]


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def close(self):
        pass


BUILT = bj(2026, 11, 9, 8, 5)
SNAP = bj(2026, 11, 9, 8, 2)


def mk(sym, kind, value, src="level", tag="日线压力位（4段接触，分析时距现价+5.00%）", note="", band=None, created=None):
    item = {"key": f"{sym}|{kind}|{value}", "sym": sym, "kind": kind, "value": value, "src": src, "tag": tag}
    if note:
        item["note"] = note
    if band:
        item["band"] = band
    if created:
        item["created"] = created
    return item


def plan_of(rules, built=BUILT, expires=None, coins=None):
    names = {r["sym"] for r in rules}
    base = {s: {"rearm_pct": 1.0, "near_pct": 0.5, "analysis": f"阶段=宽幅震荡；{s}测试分析"} for s in names}
    return {"version": 1, "day": built.strftime("%Y-%m-%d"), "built_at": ms(built), "expires_at": expires or ms(built + timedelta(hours=72)),
            "snapshot_at": ms(SNAP), "coins": {**base, **(coins or {})}, "rules": rules}


class Cycle:
    """把状态、发送记录和假行情绑在一起，方便一轮一轮往下跑。"""

    def __init__(self, plan, settings=None):
        self.plan, self.state, self.sent, self.feed = plan, autowatch.new_state(), [], Feed()
        self.settings, self.fail = settings, False

    def notify(self, text):
        if self.fail:
            raise RuntimeError("Telegram 断了")
        self.sent.append(text)

    def run(self, when, plan="keep"):
        return autowatch.run_auto_cycle(self.plan if plan == "keep" else plan, self.state, self.notify, ms(when) + 30000, self.feed,
                                        self.settings)


def up_bars(high, close, start=bj(2026, 11, 9, 20, 45)):
    return [kl(start, 100, high, 99.5, close)]


class FireTest(unittest.TestCase):
    def test_touch_fires_with_tag_analysis_and_ledger_note(self):
        rule = mk("SOL", "up", 105, src="ledger", tag="台账挂单进场价", note="台账：第31期 $SOL 做空挂单中｜进场 105｜止损 107.1｜目标 96.6")
        c = Cycle(plan_of([rule]))
        c.feed.set("SOL", "15m", up_bars(105.4, 105.2))
        summary = c.run(bj(2026, 11, 9, 21, 0))
        self.assertEqual((summary["fired"], summary["sent"], summary["sent_today"]), (1, 1, 1))
        text = c.sent[0]
        self.assertIn("【监控提醒·自动】11-09 21:00（北京时间）", text)
        self.assertIn("SOL 价格涨到 105：近15分钟最高 105.4", text)
        self.assertIn("↳ 台账挂单进场价", text)
        self.assertIn("分析（取数 11-09 08:02）：阶段=宽幅震荡；SOL测试分析", text)
        self.assertIn("台账：第31期 $SOL 做空挂单中", text)

    def test_nothing_fires_below_level_and_bars_before_creation_do_not_count(self):
        c = Cycle(plan_of([mk("SOL", "up", 105)]))
        c.feed.set("SOL", "15m", up_bars(104.9, 104.5))
        self.assertEqual(c.run(bj(2026, 11, 9, 21, 0))["fired"], 0)
        self.assertEqual(c.sent, [])
        late = Cycle(plan_of([mk("SOL", "up", 105, created=ms(bj(2026, 11, 9, 21, 10)))]))
        late.feed.set("SOL", "15m", up_bars(106, 105.5))   # 这根线 21:00 就收盘了，早于规则创建
        self.assertEqual(late.run(bj(2026, 11, 9, 21, 12))["fired"], 0)

    def test_close_confirmation_fires_once_per_closed_bar(self):
        built = bj(2026, 11, 8, 19, 5)
        c = Cycle(plan_of([mk("SOL", "dayup", 100, tag="日线压力位")], built=built))
        c.feed.set("SOL", "1d", [kl(bj(2026, 11, 8, 8, 0), 98, 101.5, 97.5, 101, minutes=1440)])
        c.feed.set("SOL", "15m", up_bars(100.5, 101))
        self.assertEqual(c.run(bj(2026, 11, 9, 10, 0))["fired"], 1)
        self.assertIn("SOL 日线收盘站上 100：收盘 101", c.sent[0])
        self.assertEqual(c.run(bj(2026, 11, 9, 10, 15))["fired"], 0)   # 同一根收盘线不重复判

    def test_near_fires_inside_band_only(self):
        c = Cycle(plan_of([mk("SOL", "near", 105, src="ledger", tag="台账挂单进场价", band=0.5)]))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 20, 45), 103, 104.2, 102.9, 104.8)])
        self.assertEqual(c.run(bj(2026, 11, 9, 21, 0))["fired"], 1)
        self.assertIn("SOL 价格接近 105（±0.5% 内）：现价 104.8，距价位 -0.19%", c.sent[0])
        far = Cycle(plan_of([mk("SOL", "near", 105, band=0.5)]))
        far.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 20, 45), 103, 103.5, 102.9, 103.2)])
        self.assertEqual(far.run(bj(2026, 11, 9, 21, 0))["fired"], 0)

    def test_surge_and_one_hour_cooldown(self):
        c = Cycle(plan_of([mk("SOL", "surge", 2, src="vol", tag="15分钟异常波动（阈值 = 0.8 × 4h ATR 2.5%）")]))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 20, 45), 100, 103, 99.9, 102.5)])
        self.assertEqual(c.run(bj(2026, 11, 9, 21, 0))["fired"], 1)
        self.assertIn("SOL 15分钟涨幅达到 2%：这根15分钟线 +2.50%", c.sent[0])
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 21, 0), 102.5, 106, 102.4, 105.5)])
        self.assertEqual(c.run(bj(2026, 11, 9, 21, 15))["fired"], 0)   # 1 小时内不再判
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 22, 0), 105.5, 109, 105.4, 108.5)])
        self.assertEqual(c.run(bj(2026, 11, 9, 22, 15))["fired"], 1)


class PostFillTest(unittest.TestCase):
    ENTRY = "SOL|up|105"

    def rules(self):
        entry = mk("SOL", "up", 105, src="ledger", tag="台账挂单进场价", note="台账：x")
        stop = mk("SOL", "up", 107.1, src="ledger", tag="止损价（成交后；按日线收盘判断，触及不等于触发）", note="台账：x")
        stop["requires"] = self.ENTRY
        r2 = mk("SOL", "down", 100.8, src="ledger", tag="2R价位（成交后）", note="台账：x")
        r2["requires"] = self.ENTRY
        return [entry, stop, r2]

    def test_stop_and_take_profit_wait_for_entry_touch_then_only_count_later_bars(self):
        c = Cycle(plan_of(self.rules()))
        # 进场价没碰到：就算价格冲到止损位、掉到 2R 位，也不提醒（还没成交）
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 20, 45), 104, 104.5, 100.0, 101.0)])
        self.assertEqual(c.run(bj(2026, 11, 9, 21, 0))["fired"], 0)
        self.assertNotIn("SOL|up|107.1", c.state["rules"])
        # 碰到进场价：只发进场价这一条
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 21, 0), 104, 105.4, 103.9, 105.2)])
        summary = c.run(bj(2026, 11, 9, 21, 15))
        self.assertEqual(summary["fired"], 1)
        self.assertIn("SOL 价格涨到 105", c.sent[0])
        # 同一根线里的 2R/3R 不补判；之后收盘的线才算：先到止损
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 21, 15), 105.2, 107.4, 105.0, 107.2)])
        summary = c.run(bj(2026, 11, 9, 21, 30))
        self.assertEqual(summary["fired"], 1)
        self.assertIn("SOL 价格涨到 107.1", c.sent[-1])
        self.assertIn("止损价（成交后", c.sent[-1])

    def test_take_profit_after_fill(self):
        c = Cycle(plan_of(self.rules()))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 21, 0), 104, 105.4, 103.9, 105.2)])
        c.run(bj(2026, 11, 9, 21, 15))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 21, 15), 105.2, 105.3, 100.5, 101.0)])
        c.run(bj(2026, 11, 9, 21, 30))
        self.assertTrue(any("SOL 价格跌到 100.8" in t and "2R价位（成交后）" in t for t in c.sent))


class RearmTest(unittest.TestCase):
    def test_same_level_needs_six_hours_and_price_leaving_before_firing_again(self):
        c = Cycle(plan_of([mk("SOL", "up", 105)]))   # rearm_pct = 1.0 → 价格要回到 103.95 以下
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 8, 45), 104, 105.4, 103.9, 105.2)])
        self.assertEqual(c.run(bj(2026, 11, 10, 9, 0))["fired"], 1)
        # 价格一直待在价位上方，1 小时后仍不重复
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 10, 15), 105.2, 105.9, 105.1, 105.5)])
        self.assertEqual(c.run(bj(2026, 11, 10, 10, 30))["fired"], 0)
        # 超过 6 小时但价格从没离开过：仍不重复
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 15, 15), 105.5, 106, 105.2, 105.8)])
        self.assertEqual(c.run(bj(2026, 11, 10, 15, 30))["fired"], 0)
        # 价格跌到价位下方足够远：重新武装，但这一轮没碰到价位
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 15, 30), 104.5, 104.5, 103, 103.2)])
        self.assertEqual(c.run(bj(2026, 11, 10, 15, 45))["fired"], 0)
        # 再涨到价位：重新提醒
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 15, 45), 103.2, 105.5, 103.2, 105.4)])
        self.assertEqual(c.run(bj(2026, 11, 10, 16, 0))["fired"], 1)

    def test_going_away_within_cooldown_rearms_once_six_hours_pass(self):
        c = Cycle(plan_of([mk("SOL", "up", 105)]))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 8, 45), 104, 105.4, 103.9, 105.2)])
        self.assertEqual(c.run(bj(2026, 11, 10, 9, 0))["fired"], 1)
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 9, 45), 104, 104, 102.5, 103)])
        c.run(bj(2026, 11, 10, 10, 0))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 10, 15), 103, 105.6, 103, 105.5)])
        self.assertEqual(c.run(bj(2026, 11, 10, 10, 30))["fired"], 0)   # 离开过，但冷却没到
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 15, 15), 105.5, 106, 105.2, 105.8)])
        self.assertEqual(c.run(bj(2026, 11, 10, 15, 30))["fired"], 1)   # 冷却到了 + 离开过 → 重新武装

    def test_state_for_rules_missing_from_new_plan_is_dropped(self):
        c = Cycle(plan_of([mk("SOL", "up", 105), mk("ETH", "up", 50)]))
        c.feed.set("SOL", "15m", up_bars(105.4, 105.2))
        c.feed.set("ETH", "15m", up_bars(40, 39))
        c.run(bj(2026, 11, 9, 21, 0))
        self.assertEqual(len(c.state["rules"]), 2)
        c.run(bj(2026, 11, 9, 21, 15), plan=plan_of([mk("SOL", "up", 105)]))
        self.assertEqual(list(c.state["rules"]), ["SOL|up|105"])


class ThrottleTest(unittest.TestCase):
    def test_quiet_hours_hold_normal_alerts_but_not_ledger_ones_and_flush_in_the_morning(self):
        rules = [mk("SOL", "up", 105), mk("ETH", "up", 105, src="ledger", tag="止损价", note="台账：x")]
        c = Cycle(plan_of(rules))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 2, 45), 104, 105.4, 103.9, 105.2)])
        c.feed.set("ETH", "15m", [kl(bj(2026, 11, 10, 2, 45), 104, 105.4, 103.9, 105.2)])
        summary = c.run(bj(2026, 11, 10, 3, 0))
        self.assertEqual((summary["fired"], summary["sent"], summary["held"]), (2, 1, 1))
        self.assertIn("ETH 价格涨到 105", c.sent[0])
        self.assertNotIn("SOL 价格涨到", c.sent[0])
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 6, 45), 105, 105.1, 104.9, 105)])
        c.feed.set("ETH", "15m", [kl(bj(2026, 11, 10, 6, 45), 105, 105.1, 104.9, 105)])
        c.run(bj(2026, 11, 10, 6, 15))
        self.assertEqual(len(c.sent), 1)   # 06:15 还在静默
        summary = c.run(bj(2026, 11, 10, 7, 0))
        self.assertEqual((summary["sent"], summary["held"]), (1, 0))
        self.assertIn("[11-10 03:00 触发，暂存后补发] SOL 价格涨到 105", c.sent[1])

    def test_quiet_can_be_turned_off_or_moved(self):
        c = Cycle(plan_of([mk("SOL", "up", 105)]), settings={"quiet": None})
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 10, 2, 45), 104, 105.4, 103.9, 105.2)])
        self.assertEqual(c.run(bj(2026, 11, 10, 3, 0))["sent"], 1)
        self.assertEqual(autowatch.parse_quiet("23:30-06:00"), (23 * 60 + 30, 6 * 60))
        self.assertTrue(autowatch.in_quiet(ms(bj(2026, 11, 10, 0, 10)), (23 * 60 + 30, 6 * 60)))
        self.assertFalse(autowatch.in_quiet(ms(bj(2026, 11, 10, 12, 0)), (23 * 60 + 30, 6 * 60)))

    def test_same_coin_once_per_hour_and_merged_within_one_round(self):
        c = Cycle(plan_of([mk("SOL", "up", 105), mk("SOL", "up", 110, tag="另一个价位"), mk("ETH", "up", 105)]))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 9, 45), 104, 105.4, 103.9, 105.2)])
        c.feed.set("ETH", "15m", [kl(bj(2026, 11, 9, 9, 45), 104, 105.4, 103.9, 105.2)])
        summary = c.run(bj(2026, 11, 9, 10, 0))
        self.assertEqual((len(c.sent), summary["sent"]), (1, 2))   # SOL、ETH 同一条消息
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 10, 15), 105.2, 110.5, 105.2, 110.2)])
        summary = c.run(bj(2026, 11, 9, 10, 30))                    # 31 分钟后 SOL 第二个价位：同币 1 小时内只发 1 次
        self.assertEqual((summary["fired"], summary["sent"], summary["held"]), (1, 0, 1))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 10, 45), 110.2, 110.3, 110, 110.1)])
        summary = c.run(bj(2026, 11, 9, 11, 0))
        self.assertEqual((summary["sent"], summary["held"]), (1, 0))
        self.assertIn("暂存后补发", c.sent[-1])
        self.assertIn("SOL 价格涨到 110", c.sent[-1])

    def test_daily_cap_blocks_normal_alerts_once_with_notice_but_lets_ledger_through(self):
        syms = ["SOL", "ETH", "BNB"]
        c = Cycle(plan_of([mk(s, "up", 105) for s in syms] + [mk("XRP", "up", 105, src="ledger", tag="止损价", note="台账：y")]),
                  settings={"cap": 2})
        for s in syms + ["XRP"]:
            c.feed.set(s, "15m", [kl(bj(2026, 11, 9, 9, 45), 104, 105.4, 103.9, 105.2)])
        summary = c.run(bj(2026, 11, 9, 10, 0))
        # 台账 XRP 先发并占 1 个名额，剩 1 个给 SOL；ETH、BNB 被压
        self.assertEqual((summary["sent"], summary["suppressed"], summary["sent_today"]), (2, 2, 2))
        self.assertIn("XRP 价格涨到", c.sent[0])
        self.assertIn("SOL 价格涨到", c.sent[0])
        self.assertEqual(c.sent[0].count("今日自动提醒已达 2 条上限"), 1)
        # 下一轮：又一个普通提醒被压，不再重复说明；台账提醒照发
        c.plan["rules"].append(mk("BTC", "up", 105))
        c.plan["rules"].append(mk("BCH", "up", 105, src="ledger", tag="目标价", note="台账：z"))
        for s in ("BTC", "BCH"):
            c.feed.set(s, "15m", [kl(bj(2026, 11, 9, 10, 15), 104, 105.4, 103.9, 105.2)])
        summary = c.run(bj(2026, 11, 9, 10, 30))
        self.assertEqual(len(c.sent), 2)
        self.assertIn("BCH 价格涨到", c.sent[1])
        self.assertNotIn("BTC 价格涨到", c.sent[1])
        self.assertNotIn("上限", c.sent[1])
        self.assertEqual(summary["suppressed"], 3)
        # 提醒日从北京时间 07:00 起算：第二天 07:00 之后计数清零
        c.feed.set("ETH", "15m", [kl(bj(2026, 11, 10, 8, 45), 104, 106, 103.9, 105.2)])
        c.run(bj(2026, 11, 10, 9, 0))
        self.assertEqual(c.state["sent_today"] >= 0 and c.state["suppressed_today"], 0)

    def test_send_failure_keeps_alerts_for_the_next_round(self):
        c = Cycle(plan_of([mk("SOL", "up", 105)]))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 9, 45), 104, 105.4, 103.9, 105.2)])
        c.fail = True
        summary = c.run(bj(2026, 11, 9, 10, 0))
        self.assertEqual((summary["sent"], summary["held"], summary["sent_today"]), (0, 1, 0))
        self.assertIn("发送提醒失败", summary["errors"][0])
        c.fail = False
        summary = c.run(bj(2026, 11, 9, 10, 15))
        self.assertEqual((summary["sent"], summary["held"], summary["sent_today"]), (1, 0, 1))
        self.assertIn("暂存后补发", c.sent[0])

    def test_held_alerts_expire_after_twelve_hours(self):
        c = Cycle(plan_of([mk("SOL", "up", 105)]))
        c.state["held"] = [{"key": "k", "sym": "SOL", "kind": "up", "src": "level", "ledger": False, "tag": "", "note": "",
                            "text": "SOL 旧提醒", "ts": ms(bj(2026, 11, 9, 8, 0))}]
        c.feed.set("SOL", "15m", up_bars(100, 100))
        summary = c.run(bj(2026, 11, 9, 20, 30))
        self.assertEqual((summary["sent"], summary["held"], summary["suppressed"]), (0, 0, 1))


class PlanStateTest(unittest.TestCase):
    def test_no_plan_or_expired_plan_does_nothing_but_still_flushes_held(self):
        c = Cycle(None)
        self.assertEqual(c.run(bj(2026, 11, 9, 21, 0))["status"], "没有计划")
        expired = plan_of([mk("SOL", "up", 105)], expires=ms(bj(2026, 11, 9, 20, 0)))
        summary = c.run(bj(2026, 11, 9, 21, 0), plan=expired)
        self.assertEqual(summary["status"], "计划已过期")
        self.assertEqual(c.feed.calls, [])
        c.state["held"] = [{"key": "k", "sym": "SOL", "kind": "up", "src": "level", "ledger": False, "tag": "", "note": "",
                            "text": "SOL 暂存的提醒", "ts": ms(bj(2026, 11, 9, 20, 40))}]
        summary = c.run(bj(2026, 11, 9, 21, 0), plan=expired)
        self.assertEqual(summary["sent"], 1)
        self.assertIn("SOL 暂存的提醒", c.sent[0])

    def test_unknown_symbol_is_reported_once_and_other_coins_still_work(self):
        c = Cycle(plan_of([mk("ZZZZ", "up", 1), mk("SOL", "up", 105)]))
        c.feed.set("SOL", "15m", up_bars(105.4, 105.2))
        first = c.run(bj(2026, 11, 9, 21, 0))
        self.assertEqual(first["fired"], 1)
        self.assertTrue(any("ZZZZ 在币安合约里找不到" in e for e in first["errors"]))
        second = c.run(bj(2026, 11, 9, 21, 15))
        self.assertFalse(any("ZZZZ" in e for e in second["errors"]))

    def test_network_failures_abort_the_rest_of_the_round(self):
        rules = [mk(f"C{i}", "up", 10) for i in range(8)]
        calls = []

        def getter(url):
            calls.append(url)
            raise OSError("timeout")
        state, sent = autowatch.new_state(), []
        summary = autowatch.run_auto_cycle(plan_of(rules), state, sent.append, ms(bj(2026, 11, 9, 21, 0)), getter)
        self.assertEqual(summary["fired"], 0)
        self.assertEqual(len(calls), autowatch.ABORT_AFTER_FAILURES * 2 * 2)   # 每个币试 2 个合约名 × 2 个域名，5 个币后放弃
        self.assertTrue(any("连续 5 次拉不到币安行情" in e for e in summary["errors"]))

    def test_state_round_trip_and_defaults(self):
        c = Cycle(plan_of([mk("SOL", "up", 105)]))
        c.feed.set("SOL", "15m", up_bars(105.4, 105.2))
        c.run(bj(2026, 11, 9, 21, 0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "auto.json"
            autowatch.save_state(c.state, path)
            self.assertEqual(autowatch.load_state(path), c.state)
            self.assertEqual(autowatch.load_state(Path(tmp) / "none.json"), autowatch.new_state())
            path.write_text("坏的")
            self.assertEqual(autowatch.load_state(path), autowatch.new_state())

    def test_settings_from_env(self):
        self.assertEqual(autowatch.settings_from_env({}), autowatch.DEFAULTS)
        got = autowatch.settings_from_env({"HOTCOIN_AUTO_QUIET": "01:00-06:30", "HOTCOIN_AUTO_CAP": "20"})
        self.assertEqual((got["quiet"], got["cap"]), ((60, 390), 20))
        self.assertIsNone(autowatch.settings_from_env({"HOTCOIN_AUTO_QUIET": "off"})["quiet"])
        bad = autowatch.settings_from_env({"HOTCOIN_AUTO_QUIET": "乱写", "HOTCOIN_AUTO_CAP": "-3"})
        self.assertEqual(bad, autowatch.DEFAULTS)


class MessageTest(unittest.TestCase):
    def test_long_rounds_are_split_into_messages_under_the_limit(self):
        syms = [f"C{i:02d}" for i in range(30)]
        long_analysis = "阶段=宽幅震荡；" + "很长的分析文字" * 25
        coins = {s: {"rearm_pct": 1.0, "analysis": long_analysis} for s in syms}
        c = Cycle(plan_of([mk(s, "up", 105) for s in syms], coins=coins), settings={"cap": 100})
        for s in syms:
            c.feed.set(s, "15m", [kl(bj(2026, 11, 9, 9, 45), 104, 105.4, 103.9, 105.2)])
        summary = c.run(bj(2026, 11, 9, 10, 0))
        self.assertEqual(summary["sent"], 30)
        self.assertGreaterEqual(len(c.sent), 3)
        self.assertTrue(all(len(t) <= 3500 for t in c.sent))
        self.assertEqual(sum(t.count("价格涨到 105") for t in c.sent), 30)

    def test_messages_never_contain_advice_wording(self):
        rules = [mk("SOL", "up", 105, src="ledger", tag="台账挂单进场价", note="台账：第31期 $SOL 做空挂单中｜进场 105｜止损 107.1｜目标 96.6"),
                 mk("SOL", "dayup", 105), mk("SOL", "surge", 2, src="vol", tag="15分钟异常波动（阈值 = 0.8 × 4h ATR 2.5%）"),
                 mk("SOL", "near", 105, band=0.5)]
        c = Cycle(plan_of(rules, coins={"SOL": {"rearm_pct": 1, "analysis": "阶段=宽幅震荡；做空筛选：门①未过"}}))
        c.feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 20, 45), 100, 106, 99.9, 105.4)])
        c.feed.set("SOL", "1d", [kl(bj(2026, 11, 8, 8, 0), 98, 106, 97.5, 105.5, minutes=1440)])
        c.plan["built_at"] = ms(bj(2026, 11, 8, 19, 5))
        c.run(bj(2026, 11, 9, 21, 0))
        self.assertTrue(c.sent)
        for text in c.sent:
            for word in BANNED:
                self.assertNotIn(word, text, word)

    def test_status_text(self):
        summary = {"status": "ok", "rules": 90, "built_at": ms(BUILT), "fired": 2, "sent_today": 3, "cap": 12, "held": 1,
                   "suppressed": 4, "errors": ["x"]}
        self.assertEqual(autowatch.status_text(summary),
                         "自动规则90条（计划 11-09 08:05 生成），本轮触发2条，今日已发3/12，暂存1条，今日压住4条，错误1")
        self.assertEqual(autowatch.status_text({"status": "计划已过期", "held": 0}), "自动规则：计划已过期")


class HostWiringTest(unittest.TestCase):
    def test_fetch_plan_over_http(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append(self.path)
                data = json.dumps({"ok": True, "plan": {"rules": [{"key": "a"}]}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {"WORKER_INGEST_URL": f"http://127.0.0.1:{server.server_port}/pipeline/snapshot", "PIPELINE_SECRET": "s"}
        try:
            self.assertEqual(host.fetch_plan(env), {"rules": [{"key": "a"}]})
        finally:
            server.shutdown()
        self.assertEqual(seen, ["/pipeline/plan"])

    def test_monitor_thread_runs_manual_and_auto_and_survives_failures(self):
        feed, sent, saved = Feed(), [], {}
        feed.set("SOL", "15m", [kl(bj(2026, 11, 9, 20, 45), 100, 105.4, 99.9, 105.2)])
        plan = plan_of([mk("SOL", "up", 105)])
        plan["expires_at"] = ms(bj(2026, 11, 12, 0))
        plan["built_at"] = 0
        patched = {"fetch_watchlist": host.fetch_watchlist, "fetch_plan_full": host.fetch_plan_full, "notify": host.notify,
                   "AUTO_STATE_FILE": host.AUTO_STATE_FILE, "PAPER_FILE": host.PAPER_FILE}
        real_http, real_run = monitor.http_json, autowatch.run_auto_cycle
        with tempfile.TemporaryDirectory() as tmp:
            try:
                host.AUTO_STATE_FILE = Path(tmp) / "auto.json"
                host.PAPER_FILE = Path(tmp) / "paper.json"
                host.fetch_watchlist = lambda env: (_ for _ in ()).throw(RuntimeError("后台 500"))   # 手动规则读失败
                host.fetch_plan_full = lambda env: {"plan": plan}
                host.notify = lambda env, text, fired=None: sent.append(text)
                monitor.http_json = feed
                h = host.Host({"WORKER_INGEST_URL": "https://x.workers.dev", "PIPELINE_SECRET": "s"})
                # 把“现在”固定在不静默的时段，避免这个测试在夜里跑时被静默拦住
                autowatch.run_auto_cycle = lambda *a, **k: real_run(*a, **{**k, "settings": {"quiet": None}}) if False else real_run(
                    a[0], a[1], a[2], ms(bj(2026, 11, 9, 21, 0)) + 30000, k["getter"], {"quiet": None})
                h.monitor()
                self.assertIn("手动规则出错：后台 500", h.status["monitor"])
                self.assertIn("自动规则", h.status["monitor"])
                self.assertEqual(len(sent), 1)
                self.assertIn("SOL 价格涨到 105", sent[0])
                self.assertTrue(host.AUTO_STATE_FILE.exists())
                # 读计划失败：沿用上次的，不抛
                host.fetch_plan_full = lambda env: (_ for _ in ()).throw(RuntimeError("断网"))
                h.monitor()
                self.assertIs(h.plan, plan)
            finally:
                autowatch.run_auto_cycle = real_run
                monitor.http_json = real_http
                for name, value in patched.items():
                    setattr(host, name, value)


if __name__ == "__main__":
    unittest.main()
