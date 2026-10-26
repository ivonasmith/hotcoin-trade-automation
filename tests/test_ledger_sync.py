import importlib.util
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ledger_sync", Path(__file__).resolve().parents[1] / "pipeline" / "ledger_sync.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def bar(day, o, h, l, c):
    ts = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)
    return (ts, o, h, l, c, 1.0)


ETH = {"issue": "35", "day": "2026-10-16", "symbol": "ETH", "tier": "慢档日线", "side": "做空",
       "size": 0.12, "entry": 2585.57, "stop": 2637.28, "target": 2378.72, "status": "挂单中"}
TODAY = date(2026, 10, 20)


class ReconcileTests(unittest.TestCase):
    def test_untouched_pending_order_needs_nothing(self):
        bars = [bar("2026-10-16", 2500, 2560, 2480, 2520), bar("2026-10-17", 2520, 2540, 2490, 2500),
                bar("2026-10-18", 2500, 2530, 2480, 2510), bar("2026-10-19", 2510, 2570, 2490, 2441),
                bar("2026-10-20", 2441, 2460, 2400, 2430)]
        self.assertEqual(module.reconcile([ETH], {"ETH": bars}, {"ETH": {"fund_ann": 8.9, "ma50": 2553}}, TODAY), [])

    def test_touch_after_publish_day_is_a_fill(self):
        bars = [bar("2026-10-16", 2500, 2560, 2480, 2520), bar("2026-10-19", 2510, 2590, 2490, 2441),
                bar("2026-10-20", 2441, 2460, 2400, 2430)]
        result = module.reconcile([ETH], {"ETH": bars}, {}, TODAY)
        self.assertEqual((result[0]["type"], result[0]["date"]), ("fill", "2026-10-19"))
        self.assertNotIn("口径不一", result[0]["text"])

    def test_touch_on_publish_day_is_flagged(self):
        bars = [bar("2026-10-16", 2500, 2600, 2480, 2520), bar("2026-10-20", 2441, 2460, 2400, 2430)]
        result = module.reconcile([ETH], {"ETH": bars}, {}, TODAY)
        self.assertIn("口径不一", result[0]["text"])

    def test_expiry_and_cancel_conditions(self):
        bars = [bar("2026-10-16", 2500, 2560, 2480, 2520), bar("2026-10-20", 2441, 2460, 2400, 2430)]
        late = module.reconcile([ETH], {"ETH": bars}, {}, date(2026, 11, 5))
        self.assertEqual(late[0]["type"], "expire")
        neg = module.reconcile([ETH], {"ETH": bars}, {"ETH": {"fund_ann": -1.9, "ma50": 2553}}, TODAY)
        self.assertEqual(neg[0]["type"], "cancel")
        above = module.reconcile([ETH], {"ETH": [bar("2026-10-19", 2500, 2575, 2480, 2560), bar("2026-10-20", 2560, 2570, 2540, 2550)]},
                                 {"ETH": {"fund_ann": 5, "ma50": 2553}}, TODAY)
        self.assertEqual(above[0]["type"], "cancel")

    def test_open_position_stop_on_daily_close(self):
        pos = {**ETH, "status": "持仓中"}
        bars = [bar("2026-10-18", 2500, 2600, 2480, 2640), bar("2026-10-19", 2640, 2650, 2600, 2650),
                bar("2026-10-20", 2650, 2660, 2600, 2610)]
        result = module.reconcile([pos], {"ETH": bars}, {}, TODAY)
        self.assertEqual((result[0]["type"], result[0]["price"]), ("stop_out", 2650))
        four_h = {**pos, "tier": "快档4h"}
        self.assertEqual(module.reconcile([four_h], {"ETH": bars}, {}, TODAY)[0]["type"], "check")

    SOL = {"issue": "36", "day": "2026-10-21", "symbol": "SOL", "tier": "慢档日线", "side": "做空", "size": 0.12,
           "entry": 106.54, "stop": 109.02, "target": 96.66, "status": "持仓中", "filled": "2026-10-21", "stop_now": None}

    def sol_bars(self, closes, last_day, old=108):
        # 20 older bars near 108 (so MA20 stays high), then the given closes, then today's unfinished bar
        from datetime import timedelta
        start = datetime.fromisoformat("2026-10-21") - timedelta(days=25)
        bars = [bar((start + timedelta(days=i)).date().isoformat(), old, old + 1, old - 1, old) for i in range(25)]
        for i, c in enumerate(closes):
            bars.append(bar(f"2026-10-{21 + i}", c + 1, c + 2, c - 1, c))
        bars.append(bar(last_day, closes[-1], closes[-1] + 1, closes[-1] - 1, closes[-1]))
        return bars

    def test_two_r_moves_stop_to_entry(self):
        # R = 109.02-106.54 = 2.48; 2R close <= 101.58
        bars = self.sol_bars([106.9, 104.0, 101.0, 102.0], "2026-10-25")
        move = [p for p in module.reconcile([self.SOL], {"SOL": bars}, {}, date(2026, 10, 25)) if p["type"] == "stop_move"]
        self.assertEqual(len(move), 1)
        self.assertEqual(move[0]["price"], 106.54)
        self.assertIn("2R", move[0]["text"])

    def test_three_r_follows_ma20_and_stop_only_goes_toward_profit(self):
        bars = self.sol_bars([106.9, 100.0, 99.0, 98.0], "2026-10-25", old=104)   # best close 98 -> 3.5R, MA20 ~103.4
        # MA20 of the last 20 completed closes is well above the last close and below entry here
        res = module.reconcile([self.SOL], {"SOL": bars}, {}, date(2026, 10, 25))
        move = next(p for p in res if p["type"] == "stop_move")
        self.assertLess(move["price"], 106.54)
        self.assertGreater(move["price"], 98.0)
        # MA20 above the entry price: the stop is capped at the entry (2R rule), never looser than that
        high = module.reconcile([self.SOL], {"SOL": self.sol_bars([106.9, 100.0, 99.0, 98.0], "2026-10-25")}, {}, date(2026, 10, 25))
        self.assertEqual(next(p for p in high if p["type"] == "stop_move")["price"], 106.54)
        # once booked, the same stop proposes nothing new
        booked = {**self.SOL, "stop_now": move["price"]}
        again = module.reconcile([booked], {"SOL": bars}, {}, date(2026, 10, 25))
        self.assertFalse([p for p in again if p["type"] == "stop_move"])
        # a stop already tighter (hand-set at 100.5, above the last close 98 but below MA20) is never loosened
        tight = {**self.SOL, "stop_now": 100.5}   # tighter than MA20 103.4
        self.assertFalse([p for p in module.reconcile([tight], {"SOL": bars}, {}, date(2026, 10, 25)) if p["type"] == "stop_move"])

    def test_below_two_r_nothing_moves_and_moved_stop_decides_the_stop_out(self):
        bars = self.sol_bars([106.9, 105.0, 104.0, 103.0], "2026-10-25")
        self.assertFalse([p for p in module.reconcile([self.SOL], {"SOL": bars}, {}, date(2026, 10, 25)) if p["type"] == "stop_move"])
        # moved stop 103.5 is crossed by the 104.0... use a close above the MOVED stop: stop-out judged on it, not on 109.02
        crossed = {**self.SOL, "stop_now": 102.5}
        res = module.reconcile([crossed], {"SOL": bars}, {}, date(2026, 10, 25))
        self.assertEqual(res[0]["type"], "stop_out")
        self.assertEqual(res[0]["price"], 103.0)

    def test_missing_market_data_is_reported_not_guessed(self):
        self.assertEqual(module.reconcile([ETH], {}, {}, TODAY)[0]["type"], "check")


if __name__ == "__main__":
    unittest.main()
