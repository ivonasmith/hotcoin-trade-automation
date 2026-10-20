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

    def test_missing_market_data_is_reported_not_guessed(self):
        self.assertEqual(module.reconcile([ETH], {}, {}, TODAY)[0]["type"], "check")


if __name__ == "__main__":
    unittest.main()
