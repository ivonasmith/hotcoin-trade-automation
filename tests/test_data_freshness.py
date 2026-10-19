import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("run_data", Path(__file__).resolve().parents[1]/"pipeline"/"run_data.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000000
        self.summary = {"指标": {s: {"price": 100} for s in module.CORE}}
        self.raw = {s: {"prem": {"time": self.now}} for s in module.CORE}

    def test_stale_core_stops_pipeline(self):
        self.raw["BTC"]["prem"]["time"] -= 1800001
        with self.assertRaises(ValueError):
            module.validate(self.summary, self.raw, self.now)

    def test_secondary_missing_is_explicitly_excluded(self):
        self.summary["指标"]["TEST"] = {"price": 5}
        result = module.validate(self.summary, self.raw, self.now)
        self.assertIn("TEST", result["excluded_symbols"])
        self.assertNotIn("TEST", result["fresh_symbols"])

    def test_invalid_prices_stop_core(self):
        for price in [None, -1, float("nan"), float("inf")]:
            self.summary["指标"]["BTC"]["price"] = price
            with self.assertRaises(ValueError):
                module.validate(self.summary, self.raw, self.now)

    def test_all_core_must_exist(self):
        del self.summary["指标"]["BTC"]
        with self.assertRaises(ValueError):
            module.validate(self.summary, self.raw, self.now)


if __name__ == "__main__":
    unittest.main()
