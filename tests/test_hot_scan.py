import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import hot_scan  # noqa: E402


def fake(now):
    def fetch(url, params=None):
        if "ticker/24hr" in url:
            return [{"symbol": "SOLUSDT", "priceChangePercent": "9.5", "quoteVolume": "3e9", "lastPrice": "150"},
                    {"symbol": "BTCUSDT", "priceChangePercent": "1", "quoteVolume": "2e10", "lastPrice": "90000"},
                    {"symbol": "XXXUSDT", "priceChangePercent": "30", "quoteVolume": "1e9", "lastPrice": "1"}]
        if "premiumIndex" in url:
            return [{"symbol": "SOLUSDT", "lastFundingRate": "0.0006"}, {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}]
        if "klines" in url:
            v = 1e9 if "SOL" in params["symbol"] else 2e10
            return [[0] * 7 + [v] for _ in range(8)] + [[0] * 7 + [1]]
        if "trending" in url:
            return {"coins": [{"item": {"symbol": "pepe", "market_cap_rank": 30}}]}
        return {"data": {"catalogs": [{"articles": [
            {"title": "币安将上线 ABC", "releaseDate": now * 1000 - 3600000},
            {"title": "币安将上线 OLD", "releaseDate": now * 1000 - 5 * 86400000},
            {"title": "系统维护", "releaseDate": now * 1000}]}]}}
    return fetch


class HotScanTests(unittest.TestCase):
    def test_board_picks_movers_only_from_universe(self):
        now = time.time()
        out = hot_scan.scan({"指标": {"SOL": {}, "BTC": {}}}, fake(now), now)
        self.assertEqual([m["标的"] for m in out["异动"]], ["SOL"])  # BTC 平淡、XXX 不在币池
        self.assertEqual(out["异动"][0]["成交额倍数"], 3.0)
        self.assertEqual(set(out["异动"][0]["标签"]), {"大涨", "放量", "费率偏正"})
        self.assertEqual(out["币安公告48h"], ["币安将上线 ABC"])
        self.assertEqual(out["缺口"], [])

    def test_failures_become_gaps_not_exceptions(self):
        def boom(url, params=None):
            raise TimeoutError()
        out = hot_scan.scan({"指标": {"BTC": {}}}, boom, time.time())
        self.assertEqual(out["异动"], [])
        self.assertEqual(len(out["缺口"]), 4)


if __name__ == "__main__":
    unittest.main()
