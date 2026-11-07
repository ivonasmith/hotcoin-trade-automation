import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
import rotation_report as rr  # noqa: E402

DAY0 = date(2025, 1, 1)
NOW = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)


QV = {}


def qv(i):
    return QV.get("v", 8e6)


def rows(closes, first=DAY0):
    out = []
    for i, c in enumerate(closes):
        start = datetime.combine(first + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc)
        ms = int(start.timestamp() * 1000)
        out.append([ms, str(c), str(c * 1.01), str(c * 0.99), str(c), "0", ms + 86400000 - 1, str(qv(i))])
    return out


def series(total, segments):
    """segments: [(天数, 终点价)] 线性连接，起点 100。"""
    out, price = [], 100.0
    for days, target in segments:
        for k in range(1, days + 1):
            out.append(price + (target - price) * k / days)
        price = target
    return out[:total] + [out[-1]] * max(0, total - len(out))


N = (date(2026, 11, 5) - DAY0).days + 1   # 数据最后一天 11-05（11-06 的日线还没收）
START, END = date(2026, 7, 1), date(2026, 10, 31)
IDX = lambda d: (d - DAY0).days


def curve(low_day, rise_day, top, down=0.5):
    """平 100 → 在 low_day 前阴跌到 80 → rise_day 起拉到 top。"""
    segs = [(IDX(low_day), 80), (IDX(rise_day) - IDX(low_day), 80), (IDX(date(2026, 10, 15)) - IDX(rise_day), top), (N - IDX(date(2026, 10, 15)), top * (1 - down * 0.1))]
    return [100.0] * 0 + series(N, [(1, 100)] + [(a, b) for a, b in segs if a > 0])


class FakeMarket:
    def __init__(self, curves, cg_extra=None):
        self.curves, self.calls = curves, []
        self.cg = [{"symbol": s.lower(), "name": s, "market_cap": 1e9 - i, "market_cap_rank": i + 1, "current_price": c[-1] * (1000 if s == "PEPE" else 1) / (1000 if s == "PEPE" else 1),
                    "price_change_percentage_24h": 3, "ath": 1e6} for i, (s, c) in enumerate(curves.items())]
        self.cg = (cg_extra or []) + self.cg

    def __call__(self, url):
        self.calls.append(url)
        if "coingecko" in url:
            return self.cg
        if "exchangeInfo" in url:
            return {"symbols": [{"baseAsset": s, "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"} for s in self.curves]}
        raw = url.split("symbol=")[1].split("&")[0]
        if not raw.endswith("USDT"):   # 真实接口要 BTCUSDT 这种合约名，只传 BTC 会 400
            raise RuntimeError("HTTP Error 400: Bad Request")
        return rows(self.curves[raw[:-4]])


class IndicatorTest(unittest.TestCase):
    def test_sma_ema_macd_basics(self):
        self.assertEqual(rr.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(rr.ema([5, 5, 5], 3), [5, 5, 5])
        dif, dea, hist = rr.macd([100] * 60)
        self.assertTrue(all(abs(x) < 1e-9 for x in dif + dea + hist))
        up = rr.macd([100 + i for i in range(80)])
        self.assertGreater(up[0][-1], 0)

    def test_weekly_takes_last_close_of_each_iso_week(self):
        dates = [date(2026, 11, 2) + timedelta(days=i) for i in range(10)]   # 周一起 10 天
        wd, wc = rr.weekly(dates, list(range(10)))
        self.assertEqual((wd, wc), ([date(2026, 11, 8), date(2026, 11, 11)], [6, 9]))

    def test_first_cross_requires_a_real_cross(self):
        d = [date(2026, 1, 1) + timedelta(days=i) for i in range(5)]
        self.assertEqual(rr.first_cross(d, [1, 3, 3, 1, 4], [2, 2, 2, 2, 2], 1, 4), d[1])
        self.assertIsNone(rr.first_cross(d, [3, 3, 3, 3, 3], [2] * 5, 1, 4))   # 一直在上方：没有“穿越”
        self.assertIsNone(rr.first_cross(d, [None, 3, 3, 3, 3], [2] * 5, 1, 4))

    def test_pct_rank(self):
        self.assertEqual(rr.pct_rank(["2026-07-01", None, "2026-08-01"], earlier_better=True), [1.0, 0.0, 0.0])
        self.assertEqual(rr.pct_rank([0.1, 0.5, 0.3]), [0.0, 1.0, 0.5])


class AnalyzeTest(unittest.TestCase):
    def bars(self, closes, first=DAY0):
        import monitor
        return monitor.parse_klines(rows(closes, first))

    def test_early_vs_late_mover(self):
        early = self.bars(curve(date(2026, 7, 5), date(2026, 7, 20), 200))
        late = self.bars(curve(date(2026, 8, 20), date(2026, 9, 5), 200))
        a, b = rr.analyze("EARLY", early, START, END), rr.analyze("LATE", late, START, END)
        self.assertLess(a["up20"], b["up20"])
        self.assertTrue("2026-07-25" <= a["up20"] <= "2026-08-05")
        self.assertTrue("2026-09-05" <= b["up20"] <= "2026-09-15")
        self.assertAlmostEqual(a["base_close"], 80, delta=0.5)
        self.assertGreater(a["peak_ret"], 1.2)
        self.assertEqual(a["peak_date"], "2026-10-15")
        self.assertLess(a["from_peak_now"], 0)
        self.assertIsNotNone(a["up100"])

    def test_ma_cross_and_structure(self):
        closes = curve(date(2026, 7, 5), date(2026, 7, 20), 200)
        res = rr.analyze("X", self.bars(closes), START, END)
        m = res["ma200"]
        self.assertTrue(m["available"])
        self.assertFalse(m["above_at_base"])
        self.assertIsNotNone(m["cross_date"])
        self.assertTrue(m["above_now"])
        self.assertTrue(res["macd_w"]["dif_above_zero_now"])
        self.assertTrue(res["structure"].startswith("强势多头") or res["structure"].startswith("修复中"))
        flat = rr.analyze("F", self.bars([100.0] * N), START, END)
        self.assertEqual(flat["ma200"]["cross_date"], None)
        self.assertIsNone(flat["up20"])

    def test_not_enough_history_or_listed_inside_window(self):
        res = rr.analyze("NEW", self.bars([1.0 + i * 0.01 for i in range(100)]), date(2025, 1, 1), date(2025, 3, 1))
        self.assertIn("skipped", res)
        short = rr.analyze("S", self.bars(curve(date(2026, 7, 5), date(2026, 7, 20), 200)[-250:], date(2026, 11, 5) - timedelta(days=249)), START, END)
        self.assertFalse(short["ma365"]["available"])
        self.assertEqual(short["both_date"], None)


class RunTest(unittest.TestCase):
    def test_full_run_outputs_report_and_data(self):
        curves = {"BTC": curve(date(2026, 7, 8), date(2026, 8, 1), 130), "SOL": curve(date(2026, 7, 5), date(2026, 7, 20), 220),
                  "AAA": curve(date(2026, 8, 25), date(2026, 9, 10), 250), "FLAT": [100.0] * N}
        extra = [{"symbol": "usdt", "name": "Tether", "market_cap": 9e12, "market_cap_rank": 1, "current_price": 1.0, "price_change_percentage_24h": 0.01, "ath": 1.2},
                 {"symbol": "wsteth", "name": "Wrapped stETH", "market_cap": 8e12, "market_cap_rank": 2, "current_price": 100, "price_change_percentage_24h": 1, "ath": 200},
                 {"symbol": "nobin", "name": "NoBinance", "market_cap": 7e12, "market_cap_rank": 3, "current_price": 5, "price_change_percentage_24h": 1, "ath": 9}]
        market = FakeMarket(curves, extra)
        with tempfile.TemporaryDirectory() as tmp:
            meta, ranked = rr.run(START, END, 3, tmp, getter=market, now=NOW)
            self.assertEqual([c["symbol"] for c in ranked].count("BTC"), 1)
            self.assertEqual(len(ranked), 3)                      # top_n = 3：BTC SOL AAA，FLAT 排在后面不要
            self.assertNotIn("FLAT", [c["symbol"] for c in ranked])
            sol = next(c for c in ranked if c["symbol"] == "SOL")
            aaa = next(c for c in ranked if c["symbol"] == "AAA")
            self.assertLess(sol["up20"], aaa["up20"])
            page = (Path(tmp) / "rotation_report.html").read_text(encoding="utf-8")
            for text in ("启动观察队列", "至今没启动", "第1批", "先涨后涨", "站上MA200日", "周线金叉日", "综合分", "SOL", "<svg"):
                self.assertIn(text, page)
            for word in ("建议买", "应该买", "建议卖"):
                self.assertNotIn(word, page)
            self.assertTrue((Path(tmp) / "rotation_data.json").stat().st_size > 1000)
            self.assertTrue(all("scores" in c and 0 <= c["composite"] <= 1 for c in ranked))

    def test_price_mismatch_is_skipped_as_namesake(self):
        curves = {"BTC": curve(date(2026, 7, 8), date(2026, 8, 1), 130), "SOL": curve(date(2026, 7, 5), date(2026, 7, 20), 220)}
        market = FakeMarket(curves)
        market.cg[1]["current_price"] = 9999
        with tempfile.TemporaryDirectory() as tmp:
            meta, ranked = rr.run(START, END, 5, tmp, getter=market, now=NOW)
            self.assertEqual([c["symbol"] for c in ranked], ["BTC"])
            self.assertIn("SOL 未纳入", meta["universe_note"])

    def test_falls_back_to_turnover_ranking_when_market_cap_list_fails(self):
        curves = {"BTC": curve(date(2026, 7, 8), date(2026, 8, 1), 130), "SOL": curve(date(2026, 7, 5), date(2026, 7, 20), 220)}
        market = FakeMarket(curves)
        original = market.__call__

        def getter(url):
            if "coingecko" in url:
                raise RuntimeError("HTTP 429")
            if "ticker/24hr" in url:
                return [{"symbol": "SOLUSDT", "quoteVolume": "5"}, {"symbol": "BTCUSDT", "quoteVolume": "9"}, {"symbol": "ETHBTC", "quoteVolume": "99"}]
            return original(url)
        rr.time.sleep = lambda s: None
        with tempfile.TemporaryDirectory() as tmp:
            meta, ranked = rr.run(START, END, 5, tmp, getter=getter, now=NOW)
            self.assertEqual({c["symbol"] for c in ranked}, {"BTC", "SOL"})
            self.assertTrue(any("成交额" in w for w in meta["warnings"]))


if __name__ == "__main__":
    unittest.main()


class ActivityTest(unittest.TestCase):
    def test_low_turnover_flagged_with_threshold_and_report_section(self):
        curves = {"BTC": curve(date(2026, 7, 8), date(2026, 8, 1), 130), "THIN": curve(date(2026, 7, 5), date(2026, 7, 20), 220)}
        market = FakeMarket(curves)
        market.cg[1]["total_volume"] = 2_000_000
        market.cg[0]["total_volume"] = 90_000_000
        base_call = market.__call__

        def getter(url):
            if "symbol=THIN" in url:
                QV["v"] = 3e6
            else:
                QV["v"] = 8e6
            return base_call(url)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                meta, ranked = rr.run(START, END, 5, tmp, getter=getter, now=NOW)
                by = {c["symbol"]: c for c in ranked}
                self.assertTrue(by["THIN"]["low_activity"])
                self.assertFalse(by["BTC"]["low_activity"])
                self.assertAlmostEqual(by["THIN"]["turnover"]["median"], 3e6, delta=1)
                self.assertTrue(by["THIN"]["low_now"])
                page = (Path(tmp) / "rotation_report.html").read_text(encoding="utf-8")
                self.assertIn("低活跃", page)
                self.assertIn("300万", page)
                self.assertIn("成交活跃度：窗口内币安永续日成交额中位数低于 500万", page)
                meta2, ranked2 = rr.run(START, END, 5, tmp, getter=getter, now=NOW, min_turnover=1e6)
                self.assertFalse(any(c["low_activity"] for c in ranked2))
        finally:
            QV.clear()

    def test_money_format(self):
        self.assertEqual(rr.money(5e6), "500万")
        self.assertEqual(rr.money(2.5e8), "2.5亿")
        self.assertEqual(rr.money(None), "—")


class QueueTest(unittest.TestCase):
    def test_batches_late_and_waiting(self):
        curves = {"BTC": curve(date(2026, 7, 8), date(2026, 8, 1), 130), "SOL": curve(date(2026, 7, 5), date(2026, 7, 20), 220),
                  "AAA": curve(date(2026, 8, 25), date(2026, 9, 10), 250), "FLAT": [100.0] * N,
                  "LATE": curve(date(2026, 7, 5), date(2026, 11, 1), 200)}
        curves["LATE"] = [100.0 - min(i, 300) * 0 for i in range(N)]
        for i in range(IDX(date(2026, 11, 1)), N):   # 区间结束（10-31）后才拉起来
            curves["LATE"][i] = 100.0 * (1 + 0.1 * (i - IDX(date(2026, 11, 1)) + 1))
        market = FakeMarket(curves)
        with tempfile.TemporaryDirectory() as tmp:
            meta, ranked = rr.run(START, END, 10, tmp, getter=market, now=NOW)
            by = {c["symbol"]: c for c in ranked}
            started, late, waiting = rr.launch_queue(ranked, by["BTC"])
            self.assertEqual([c["symbol"] for c in started][0], "SOL")
            self.assertEqual(by["SOL"]["batch"], 1)
            self.assertEqual([c["symbol"] for c in started], ["SOL", "BTC", "AAA"])
            self.assertEqual(by["AAA"]["batch"], 3)
            self.assertEqual([c["symbol"] for c in late], ["LATE"])
            self.assertEqual([c["symbol"] for c in waiting], ["FLAT"])
            self.assertEqual(by["FLAT"]["queue"], "未启动")
            self.assertEqual(by["LATE"]["queue"], "区间后启动")
            page = (Path(tmp) / "rotation_report.html").read_text(encoding="utf-8")
            self.assertIn("区间结束后才启动（共 1 个）", page)
            self.assertIn("至今没启动（共 1 个", page)
