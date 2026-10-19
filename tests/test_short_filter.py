import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "pipeline" / "vendor" / "做空筛选.py"
SPEC = importlib.util.spec_from_file_location("short_filter", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sample(fund_7d_ann):
    return {
        "price": 100,
        "阶段": "正常",
        "ma20": 110,
        "ma50": 120,
        "ma200": 90,
        "dev200": 11.1,
        "rsi_d": 50,
        "fund_ann": 8.0,
        "fund_7d_ann": fund_7d_ann,
        "fund_30d_ann": 2.0,
        "d1_above": [],
    }


class ShortFilterTests(unittest.TestCase):
    def test_path_a_rejects_negative_seven_day_average_even_if_spot_rate_positive(self):
        result = MODULE.扫一个币("XRP", sample(-0.55), verbose=False)
        self.assertIs(result["费率否决"], True)
        self.assertIn("近7日平均费率-0.6%为负", result["门③路径"])

    def test_path_a_rejects_missing_seven_day_average(self):
        result = MODULE.扫一个币("XRP", sample(None), verbose=False)
        self.assertIs(result["费率否决"], True)
        self.assertIn("近7日平均费率缺失", result["门③路径"])

    def test_path_a_accepts_nonnegative_seven_day_average(self):
        result = MODULE.扫一个币("XRP", sample(0.0), verbose=False)
        self.assertIs(result["费率否决"], False)
        self.assertTrue(result["门③路径"].startswith("路A"))
