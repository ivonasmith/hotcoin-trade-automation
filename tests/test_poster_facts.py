import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import poster_facts as pf  # noqa: E402


def bar(day, close):
    ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    return (ts, close, close, close, close, 1.0)


STATE = {"leg_start": "2026-10-01", "leg_start_close": 100.0, "alt_d0": "2026-08-27", "alt_d0_close": "80,250",
         "alt_cleared": "2026-09-26", "alt_top3": "甲", "alt_top3_en": "A"}
CLOSES = [(1, 100), (2, 110), (3, 99), (4, 120), (5, 114), (6, 118)]  # day 6 is today's forming bar


class FactsTest(unittest.TestCase):
    def bars(self, closes=CLOSES):
        return [bar(date(2026, 10, d), c) for d, c in closes]

    def test_leg_numbers_from_completed_closes(self):
        facts = pf.compute(STATE, self.bars(), date(2026, 10, 6))
        leg = facts["leg"]
        self.assertEqual(leg["day"], 5)
        self.assertAlmostEqual(leg["peak_pct"], 20.0)
        self.assertEqual(leg["peak_note_zh"], "10-04最高收盘120，未刷新")
        self.assertEqual(leg["peak_note_en"], "Oct 4 peak close 120, not exceeded")
        self.assertAlmostEqual(leg["dd"], 10.0)
        self.assertEqual(leg["dd_date"], "10-03")
        self.assertEqual(facts["alt"]["day"], 40)
        self.assertEqual(facts["alt"]["d0"], "08-27")

    def test_new_high_today(self):
        closes = CLOSES[:5] + [(6, 130), (7, 131)]
        facts = pf.compute(STATE, self.bars(closes), date(2026, 10, 7))
        self.assertEqual(facts["leg"]["peak_note_zh"], "10-06最高收盘130，今日刷新")

    def test_start_close_mismatch_and_gap_are_errors(self):
        with self.assertRaises(ValueError):
            pf.compute(dict(STATE, leg_start_close=90.0), self.bars(), date(2026, 10, 6))
        with self.assertRaises(ValueError):
            pf.compute(STATE, self.bars(CLOSES[1:]), date(2026, 10, 6))

    def test_main_never_fails_the_workflow(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmp:
            def boom(*a):
                raise RuntimeError("网络不通")
            self.assertEqual(pf.main(tmp, fetch=boom), 0)
            self.assertFalse(json.loads((Path(tmp) / "poster_facts.json").read_text())["ok"])


if __name__ == "__main__":
    unittest.main()
