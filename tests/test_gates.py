import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "tests"))
import gates  # noqa: E402
import paper  # noqa: E402
from test_autowatch import Feed, kl  # noqa: E402
from test_paper import alert, card  # noqa: E402

UTC = timezone.utc
NOW = int(datetime(2026, 11, 9, 4, 0, tzinfo=UTC).timestamp() * 1000)   # 北京 12:00
DAY = 86400000


def ev(kind, when, title=None):
    return {"type": kind, "date": when[:10], "time_utc": when, "title_zh": title or kind}


def make_feed(events=None, btc=None, cal_ok=True):
    feed = Feed()
    if btc is not None:
        feed.set("BTC", "1d", btc)
    def getter(url):
        if "cal_latest" in url:
            if not cal_ok:
                raise OSError("down")
            return {"today": "2026-11-09", "btc": {"events": events or []}}
        return feed(url)
    return getter


def btc_bars(closes):
    start = datetime(2026, 11, 9, tzinfo=UTC) - timedelta(days=len(closes))
    return [kl(start + timedelta(days=i), c, c * 1.01, c * 0.99, c, minutes=1440) for i, c in enumerate(closes)]


def plan_with(leg=None, built=NOW - 3600000):
    return {"built_at": built, "macro": {"leg": leg or {"ok": True, "start": "2026-06-30", "day": 100, "dd": 6.9}}}


CALM = [100.0] * 40
FALL = [100.0] * 30 + [100 - i * 1.2 for i in range(10)]   # 最后 7 天跌 ≈ 8%，收盘在 MA20 下方


class ParseTest(unittest.TestCase):
    def test_only_binary_events_kept(self):
        self.assertIsNotNone(gates.parse_event(ev("FOMC", "2026-11-10 19:00")))
        self.assertIsNotNone(gates.parse_event({"type": "未检验", "date": "2026-11-10", "title_zh": "国会法案投票"}))
        self.assertIsNotNone(gates.parse_event({"type": "REG", "date": "2026-11-10", "title_zh": "参议院市场结构法案终审"}))
        self.assertIsNone(gates.parse_event({"type": "未检验", "date": "2026-11-10", "title_zh": "某代币解锁"}))
        self.assertIsNone(gates.parse_event({"type": "FOMC", "date": "bad"}))


class GateTest(unittest.TestCase):
    def check(self, getter, side="多", plan=None, **card_kw):
        c = card(side=side, **card_kw)
        c["atr_d_pct"] = 4.0
        c["level"] = {"dist": -3.0}
        return gates.Gates(getter).check(plan or plan_with(), c, NOW)

    def test_leg_over_89_days_without_10pct_break_closes_long_gate_only(self):
        out = self.check(make_feed([], btc_bars(CALM)))
        self.assertTrue(any("涨腿第131天" in b or "涨腿第" in b for b in out["blocks"]), out)
        short = self.check(make_feed([], btc_bars(CALM)), side="空")
        self.assertFalse(any("涨腿" in b for b in short["blocks"]))      # 89 天线只管多头

    def test_leg_broken_by_10pct_does_not_light(self):
        out = self.check(make_feed([], btc_bars(CALM)), plan=plan_with({"ok": True, "start": "2026-06-30", "day": 120, "dd": 12.0}))
        self.assertEqual([b for b in out["blocks"] if "涨腿" in b], [])
        self.assertTrue(any("打断" in p for p in out["passes"]))

    def test_young_leg_ok(self):
        out = self.check(make_feed([], btc_bars(CALM)), plan=plan_with({"ok": True, "start": "2026-10-01", "day": 40, "dd": 3.0}))
        self.assertEqual(out["blocks"], [])
        self.assertTrue(any("没过89天线" in p for p in out["passes"]))

    def test_missing_leg_is_unchecked_not_blocked(self):
        out = self.check(make_feed([], btc_bars(CALM)), plan={"built_at": NOW, "macro": {"leg": {"ok": False}}})
        self.assertTrue(any("涨跌腿" in u for u in out["unchecked"]))
        self.assertEqual([b for b in out["blocks"] if "涨腿" in b], [])

    def test_down_leg_never_lights_the_89_day_line(self):
        down = plan_with({"ok": True, "dir": "down", "start": "2026-09-21", "day": 120, "dd": None, "drop_pct": 14.8})
        out = self.check(make_feed([], btc_bars(CALM)), plan=down)
        self.assertEqual([b for b in out["blocks"] if "腿" in b], [], "89 天线只管涨腿；跌腿天数再长也不亮")
        self.assertTrue(any("跌腿第120天" in p and "14.8%" in p for p in out["passes"]), out)

    def test_up_leg_uses_the_website_day_count(self):
        up = plan_with({"ok": True, "dir": "up", "start": "2026-06-30", "day": 89, "dd": 6.9})
        out = self.check(make_feed([], btc_bars(CALM)), plan=up)
        self.assertEqual([b for b in out["blocks"] if "涨腿" in b], [], "网站第 89 天还没过线（> 89 才亮）")
        up90 = plan_with({"ok": True, "dir": "up", "start": "2026-06-30", "day": 90, "dd": 6.9})
        self.assertTrue(any("涨腿第90天" in b for b in self.check(make_feed([], btc_bars(CALM)), plan=up90)["blocks"]))

    def test_ma20_and_7day_drop_gate(self):
        young = plan_with({"ok": True, "start": "2026-10-01", "day": 40, "dd": 3.0})
        out = self.check(make_feed([], btc_bars(FALL)), plan=young)
        self.assertTrue(any("MA20" in b and "7日跌" in b for b in out["blocks"]), out)
        calm = self.check(make_feed([], btc_bars(CALM)), plan=young)
        self.assertEqual(calm["blocks"], [])

    def test_two_events_within_48h_close_gate_one_does_not(self):
        young = plan_with({"ok": True, "start": "2026-10-01", "day": 40, "dd": 3.0}, built=NOW - 20 * DAY // 1)
        two = [ev("CPI", "2026-11-10 13:30"), ev("FOMC", "2026-11-10 19:00")]
        out = self.check(make_feed(two, btc_bars(CALM)), plan=young)
        self.assertTrue(any("二元事件叠加" in b for b in out["blocks"]), out)
        one = self.check(make_feed(two[:1], btc_bars(CALM)), plan=young)
        self.assertFalse(any("二元事件叠加" in b for b in one["blocks"]))

    def test_event_window_day_of_and_next_day_blocks_everything(self):
        placed = int(datetime(2026, 11, 10, 3, 0, tzinfo=UTC).timestamp() * 1000)
        out = self.check(make_feed([ev("CPI", "2026-11-10 13:30", "美国CPI")], btc_bars(CALM)), side="空", plan=plan_with(built=placed))
        self.assertTrue(any("当天/次日不挂任何新单" in b for b in out["blocks"]), out)
        nxt = int(datetime(2026, 11, 11, 3, 0, tzinfo=UTC).timestamp() * 1000)
        out = self.check(make_feed([ev("CPI", "2026-11-10 13:30")], btc_bars(CALM)), side="空", plan=plan_with(built=nxt))
        self.assertTrue(any("当天/次日" in b for b in out["blocks"]))

    def test_event_window_three_days_before_only_blocks_near_price_orders(self):
        placed = int(datetime(2026, 11, 8, 3, 0, tzinfo=UTC).timestamp() * 1000)   # 事件前 2 天
        events = [ev("FOMC", "2026-11-10 19:00")]
        near = self.check(make_feed(events, btc_bars(CALM)), side="空", plan=plan_with(built=placed))   # dist 3% < ATR 4% → 近价
        self.assertTrue(any("前3天" in b for b in near["blocks"]), near)
        far = gates.Gates(make_feed(events, btc_bars(CALM)))
        c = card(side="空"); c["atr_d_pct"] = 2.0; c["level"] = {"dist": 5.0}
        self.assertEqual(far.check(plan_with(built=placed), c, NOW)["blocks"], [])                       # 5% > 2%：远档照挂

    def test_calendar_down_is_unchecked_and_does_not_block(self):
        young = plan_with({"ok": True, "start": "2026-10-01", "day": 40, "dd": 3.0})
        out = self.check(make_feed(btc=btc_bars(CALM), cal_ok=False), plan=young)
        self.assertEqual(out["blocks"], [])
        self.assertTrue(any("事件日历" in u for u in out["unchecked"]))

    def test_calendar_cached_for_an_hour(self):
        calls = []
        base = make_feed([], btc_bars(CALM))
        def counting(url):
            calls.append(url)
            return base(url)
        g = gates.Gates(counting)
        g.events(NOW); g.events(NOW + 1000)
        self.assertEqual(sum(1 for u in calls if "cal_latest" in u), 1)


class PaperWithGatesTest(unittest.TestCase):
    def test_gate_block_turns_qualified_card_into_shadow_with_reason_and_lists_checked_items(self):
        with tempfile.TemporaryDirectory() as folder:
            book = paper.Paper(Path(folder) / "p.json", gates=gates.Gates(make_feed([], btc_bars(CALM))))
            c = card()
            c["atr_d_pct"] = 4.0
            item = alert(c, ts=NOW)
            plan = {**plan_with(), "cards": {c["key"]: c}}
            result = book.handle_alert(item, plan, NOW, lambda url: (_ for _ in ()).throw(OSError()) if "cal_latest" not in url else make_feed([], btc_bars(CALM))(url))
            self.assertEqual(result["cls"], "shadow")
            self.assertIn("宏观闸门·涨腿第", "\n".join(result["lines"]))

    def test_all_clear_opens_paper_trade_and_shows_checked_items(self):
        with tempfile.TemporaryDirectory() as folder:
            young = plan_with({"ok": True, "start": "2026-10-01", "day": 40, "dd": 3.0})
            book = paper.Paper(Path(folder) / "p.json", gates=gates.Gates(make_feed([], btc_bars(CALM))))
            c = card()
            c["atr_d_pct"] = 4.0
            result = book.handle_alert(alert(c, ts=NOW), {**young, "cards": {c["key"]: c}}, NOW, lambda url: (_ for _ in ()).throw(OSError()))
            self.assertEqual(result["cls"], "paper", result["lines"])
            text = "\n".join(result["lines"])
            self.assertIn("没过89天线", text)
            self.assertIn("监管/立法二元投票", text)
            self.assertNotIn("涨腿89天线、MA20", text)


if __name__ == "__main__":
    unittest.main()
