import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import poster_facts as pf  # noqa: E402

STATE = {"alt_d0": "2026-08-27", "alt_d0_close": "80,250", "alt_cleared": "2026-09-26", "alt_top3": "甲", "alt_top3_en": "A"}

# 网站 leg_latest.json（schema 4）里的 btc_leg，取自 2026-11-11 的真实读数
DOWN = {"schema": 4, "asof": "2026-11-11", "btc_leg": {
    "direction": "down",
    "leg": {"start_date": "2026-09-21", "start_close": 86620.0, "confirm_date": "2026-10-30", "day": 51, "drop_pct": 14.8,
            "low_close": 73796.06, "low_date": "2026-11-10", "btc_close": 74015.19, "from_peak_pct": -14.6,
            "reversal_line": 81175.67, "to_reversal_pct": 9.7},
    "badge": None, "prev_leg": {"dir": "up", "start_date": "2026-06-30", "end_date": "2026-09-21", "days": 83, "pct": 47.8}}}
# 2026-09-28 的涨腿读数（第 90 天，网站亮「多头只减不加」）
UP = {"schema": 4, "asof": "2026-09-28", "btc_leg": {
    "direction": "up",
    "leg": {"start_date": "2026-06-30", "start_close": 58624.71, "confirm_date": "2026-07-14", "day": 90, "gain_pct": 47.8,
            "high_close": 86620.0, "high_date": "2026-09-21", "max_dd_pct": 6.9, "max_dd_date": "2026-09-15"},
    "badge": {"zh": "多头只减不加", "en": "Longs: trim only"}, "prev_leg": None}}


class FactsTest(unittest.TestCase):
    def test_down_leg_is_copied_from_the_website(self):
        facts = pf.compute(STATE, DOWN, date(2026, 11, 13))
        leg = facts["leg"]
        self.assertEqual((leg["dir"], leg["day"], leg["start"], leg["start_close"], leg["confirm"]), ("down", 51, "2026-09-21", 86620.0, "2026-10-30"))
        self.assertEqual((leg["drop_pct"], leg["from_peak_pct"], leg["reversal_line"], leg["to_reversal_pct"]), (14.8, -14.6, 81175.67, 9.7))
        self.assertEqual((leg["low_close"], leg["low_date"], leg["asof"]), (73796.06, "2026-11-10", "2026-11-11"))
        self.assertIsNone(leg["badge_zh"])
        self.assertNotIn("dd", leg, "跌腿没有腿内回撤，不能拿涨腿的字段冒充")
        self.assertEqual(facts["alt"]["day"], 78)

    def test_up_leg_keeps_peak_drawdown_and_website_badge(self):
        leg = pf.compute(STATE, UP, date(2026, 9, 29))["leg"]
        self.assertEqual((leg["dir"], leg["day"], leg["peak_pct"], leg["dd"], leg["dd_date"]), ("up", 90, 47.8, 6.9, "09-15"))
        self.assertEqual(leg["peak_note_zh"], "09-21最高收盘86,620，未刷新")
        self.assertEqual(leg["peak_note_en"], "Sep 21 peak close 86,620, not exceeded")
        self.assertEqual((leg["badge_zh"], leg["badge_en"]), ("多头只减不加", "Longs: trim only"))

    def test_stale_or_missing_direction_is_an_error(self):
        with self.assertRaises(ValueError):
            pf.compute(STATE, DOWN, date(2026, 11, 15))          # 截至 11-11，比今天早 4 天 = 停更
        with self.assertRaises(ValueError):
            pf.compute(STATE, {"asof": "2026-11-11", "btc_leg": {"direction": "none", "leg": None}}, date(2026, 11, 12))

    def test_main_never_fails_the_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            def boom(*a):
                raise RuntimeError("网络不通")
            self.assertEqual(pf.main(tmp, fetch=boom), 0)
            self.assertFalse(json.loads((Path(tmp) / "poster_facts.json").read_text())["ok"])


if __name__ == "__main__":
    unittest.main()
