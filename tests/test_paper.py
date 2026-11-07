import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "tests"))
import autowatch  # noqa: E402
import paper  # noqa: E402
from test_autowatch import Feed, kl  # noqa: E402

BJ = timezone(timedelta(hours=8))
DAY = 86400000
T0 = int(datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)   # 一根日线的开盘（UTC 0 点）


def bar(day, o, h, l, c):
    start = T0 + day * DAY
    return {"open_time": start, "open": o, "high": h, "low": l, "close": c, "close_time": start + DAY - 1}


def trade(side="多", entry=100.0, stop=98.0, target=108.0, ts=None, notional=2000, paper_=True):
    risk = abs(entry - stop)
    sign = 1 if side == "多" else -1
    return {"id": 1, "side": side, "sym": "SOL", "paper": paper_, "entry": entry, "stop0": stop, "target": target,
            "r2": entry + sign * 2 * risk, "r3": entry + sign * 3 * risk, "stop_pct": risk / entry * 100,
            "notional_usd": notional, "ts": T0 + DAY // 2 if ts is None else ts, "status": "open", "stop_now": stop,
            "stage": 0, "exit": None, "blocks": [], "passes": [], "size": 1.0, "margin_usd": notional // 2,
            "risk_usd": 40, "mfe_r": 0, "mae_r": 0}


NOW = T0 + 40 * DAY


class SimulateTest(unittest.TestCase):
    def test_long_target_touch(self):
        bars = [bar(0, 100, 101, 99, 100.5), bar(1, 100.5, 108.5, 100, 107)]
        out = paper.simulate(trade(), bars, NOW)
        self.assertEqual(out["exit"]["reason"], "target")
        self.assertEqual(out["r"], 4.0)
        self.assertAlmostEqual(out["pnl_usd"], 2000 * 0.08 - 2, places=2)

    def test_wick_below_stop_is_not_a_stop(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 97, 99)]   # 影线到 97，收盘 99 > 98
        out = paper.simulate(trade(), bars, NOW)
        self.assertEqual(out["status"], "open")
        self.assertEqual(out["mae_r"], 1.5)

    def test_close_below_stop_exits_at_close(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 100, 100.5, 95, 96)]
        out = paper.simulate(trade(), bars, NOW)
        self.assertEqual(out["exit"]["reason"], "stop")
        self.assertEqual(out["r"], -2.0)   # 收盘 96，比止损 98 更差，按收盘价出
        self.assertLess(out["pnl_usd"], -80)

    def test_stop_and_target_same_bar_counts_stop(self):
        bars = [bar(0, 100, 109, 97, 97.5)]
        out = paper.simulate(trade(), bars, NOW)
        self.assertEqual(out["exit"]["reason"], "stop")

    def test_two_r_moves_stop_to_entry_from_next_bar(self):
        bars = [bar(0, 100, 104.5, 99.5, 103), bar(1, 103, 103.5, 99, 99.8)]   # 第一根碰到 2R(104)，第二根收盘跌破进场价
        out = paper.simulate(trade(), bars, NOW)
        self.assertEqual(out["exit"]["reason"], "stop")
        self.assertEqual(out["stage"], 1)
        self.assertAlmostEqual(out["r"], -0.1, places=2)

    def test_three_r_trails_ma20(self):
        bars = [bar(i, 100, 100.5, 99.5, 100) for i in range(20)]
        bars.append(bar(20, 100, 106.5, 100, 106))    # 碰到 3R(106) 但没到目标 108
        bars.append(bar(21, 106, 106.5, 105, 105.5))
        bars.append(bar(22, 105.5, 105.6, 99, 99.5))  # 收盘跌破 MA20(约 100.4) 的跟随止损
        out = paper.simulate(trade(ts=T0 - 1), bars, NOW, {"max_hold_days": 60})
        self.assertEqual(out["stage"], 2)
        self.assertEqual(out["exit"]["reason"], "stop")
        self.assertGreater(out["stop_now"], 100)

    def test_short_mirrors(self):
        t = trade("空", 100, 102, 92)
        out = paper.simulate(t, [bar(0, 100, 100.5, 91.5, 93)], NOW)
        self.assertEqual(out["exit"]["reason"], "target")
        self.assertEqual(out["r"], 4.0)
        out = paper.simulate(t, [bar(0, 100, 103, 99, 101.5)], NOW)   # 影线 103 过止损，收盘 101.5 没过
        self.assertEqual(out["status"], "open")
        out = paper.simulate(t, [bar(0, 100, 103, 99, 102.5)], NOW)
        self.assertEqual(out["exit"]["reason"], "stop")

    def test_timeout_after_20_closed_bars(self):
        bars = [bar(i, 100, 100.5, 99.5, 100.2) for i in range(25)]
        out = paper.simulate(trade(ts=T0 - 1), bars, NOW)
        self.assertEqual(out["exit"]["reason"], "timeout")
        self.assertEqual(out["days_held"], 20)

    def test_forming_bar_does_not_trigger_close_stop(self):
        bars = [bar(0, 100, 101, 99, 100), bar(39, 100, 100, 95, 96)]   # 最后一根还没收盘
        bars[1]["close_time"] = NOW + 1000
        out = paper.simulate(trade(), bars, NOW)
        self.assertEqual(out["status"], "open")
        self.assertLess(out["floating_r"], 0)

    def test_bars_before_fill_are_ignored(self):
        bars = [bar(0, 100, 120, 80, 100), bar(1, 100, 101, 99.5, 100)]
        out = paper.simulate(trade(ts=T0 + DAY + 100), bars, NOW)   # 成交在第二根线内，第一根不算
        self.assertEqual(out["status"], "open")


def card(sym="SOL", side="多", qualified=True, blocks=None, entry=100.0, stop=98.0, target=108.0, notional=2000, key=None):
    sign = 1 if side == "多" else -1
    risk = abs(entry - stop)
    return {"key": key or f"{sym}|{'down' if side == '多' else 'up'}|{entry:.6g}", "sym": sym, "side": side, "entry": entry,
            "stop": stop, "target": target, "r2": entry + sign * 2 * risk, "r3": entry + sign * 3 * risk,
            "stop_pct": risk / entry * 100, "target_pct": risk / entry * 400, "size": notional / 2000,
            "size_parts": "方向×1 位置×1 L1折扣×1", "notional_usd": notional, "margin_usd": notional // 2,
            "risk_usd": notional * risk / entry / 100 * 100, "risk_pct": 0.4, "qualified": qualified,
            "blocks": blocks or [], "passes": ["价格在MA200上方 +11.00%（顺势）"], "level": {"segs": 4},
            "unchecked": ["宏观闸门：涨腿89天线、MA20+7日跌幅、48小时二元事件（程序读不到，亮灯则这单作废，需你核对）"]}


def alert(c, kind="down", src="level", ts=T0 + DAY // 2):
    return {"key": c["key"], "sym": c["sym"], "kind": kind, "src": src, "tag": "日线支撑位", "ts": ts, "text": "x", "ledger": src == "ledger"}


def no_price(url):
    raise OSError("offline")


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "paper.json"
        self.book = paper.Paper(self.path)

    def plan(self, *cards):
        return {"cards": {c["key"]: c for c in cards}}

    def test_qualified_alert_opens_paper_trade_with_explicit_numbers(self):
        c = card()
        result = self.book.handle_alert(alert(c), self.plan(c), T0 + DAY // 2, no_price)
        self.assertEqual(result["cls"], "paper")
        text = "\n".join(result["lines"])
        for needle in ("纸面单 #", "进场 100", "止损 98", "止盈 108", "2R 104", "3R 106", "名义 $2,000", "保证金 $1,000",
                       "止损亏约 $40", "影线不算", "未核对"):
            self.assertIn(needle, text)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["trades"]), 1)
        self.assertTrue(saved["trades"][0]["paper"])

    def test_blocked_card_becomes_shadow_and_says_why(self):
        c = card(qualified=False, blocks=["价格在MA200下方（-3.00%）：逆势，不抄底"])
        result = self.book.handle_alert(alert(c), self.plan(c), T0, no_price)
        self.assertEqual(result["cls"], "shadow")
        self.assertIn("未过闸门：价格在MA200下方", "\n".join(result["lines"]))
        self.assertFalse(self.book.state["trades"][0]["paper"])

    def test_non_touch_and_ledger_alerts_do_not_open_trades(self):
        c = card()
        for kind, src, expect in (("dayup", "level", "info"), ("surge", "vol", "info"), ("down", "ledger", "ledger")):
            result = self.book.handle_alert(alert(c, kind, src), self.plan(c), T0, no_price)
            self.assertEqual(result["cls"], expect)
        self.assertEqual(self.book.state["trades"], [])
        self.assertEqual(len(self.book.state["alerts"]), 3)

    def test_touch_without_card_is_record_only(self):
        c = card()
        other = alert({**c, "key": "ZZZ|down|1"})
        result = self.book.handle_alert(other, self.plan(c), T0, no_price)
        self.assertEqual(result["cls"], "info")
        self.assertIn("没有机会卡", result["lines"][0])

    def test_one_paper_position_per_coin(self):
        c = card()
        self.book.handle_alert(alert(c), self.plan(c), T0, no_price)
        second = self.book.handle_alert(alert(c, ts=T0 + 1), self.plan(c), T0 + 1, no_price)
        self.assertEqual(second["cls"], "shadow")
        self.assertIn("已有一张纸面持仓", "\n".join(second["lines"]))

    def test_notional_cap_20000(self):
        for i in range(10):
            c = card(sym=f"C{i}", notional=2000, key=f"C{i}|down|100")
            self.assertEqual(self.book.handle_alert(alert(c), self.plan(c), T0, no_price)["cls"], "paper")
        c = card(sym="C10", notional=2000, key="C10|down|100")
        over = self.book.handle_alert(alert(c), self.plan(c), T0, no_price)
        self.assertEqual(over["cls"], "shadow")
        self.assertIn("超过 $20,000 上限", "\n".join(over["lines"]))

    def test_major_same_side_cap_3_and_locked_not_counted(self):
        names = ["BTC", "ETH", "SOL", "XRP"]
        results = []
        for name in names:
            c = card(sym=name, key=f"{name}|down|100")
            results.append(self.book.handle_alert(alert(c), self.plan(c), T0, no_price)["cls"])
        self.assertEqual(results, ["paper", "paper", "paper", "shadow"])
        self.book.state["trades"][0]["stop_now"] = 101.0   # BTC 这张止损上移越过进场价 → 利润已锁定，不占名额
        c = card(sym="XRP", key="XRP|down|100b")
        self.assertEqual(self.book.handle_alert(alert(c, ts=T0 + 5), self.plan(c), T0 + 5, no_price)["cls"], "paper")
        short = card(sym="BNB", side="空", key="BNB|up|100", entry=100, stop=102, target=92)
        self.assertEqual(self.book.handle_alert(alert(short, "up"), self.plan(short), T0, no_price)["cls"], "paper")   # 方向不同不占多单名额

    def close_trade(self, loss=True, sym="SOL", pnl=-10.0):
        c = card(sym=sym, key=f"{sym}|down|{self.book.state['next_id']}")
        result = self.book.handle_alert(alert(c, ts=T0 + DAY // 2), self.plan(c), T0 + DAY // 2, no_price)
        t = next(x for x in self.book.state["trades"] if x["id"] == result["trade"])
        t.update(status="closed", stage=0, exit={"reason": "stop" if loss else "target", "price": 98, "ts": NOW - DAY},
                 r=-1.0 if loss else 4.0, pnl_usd=pnl if loss else 158.0, mfe_r=0, days_held=2)
        return t

    def test_circuit_breaker_half_after_three_stops_then_halt_after_five(self):
        for _ in range(3):
            self.close_trade()
        c = card(sym="DOGE", key="DOGE|down|1", notional=2000)
        half = self.book.handle_alert(alert(c, ts=NOW), self.plan(c), NOW, no_price)
        self.assertEqual(half["cls"], "paper")
        trade_ = next(t for t in self.book.state["trades"] if t["id"] == half["trade"])
        self.assertEqual(trade_["notional_usd"], 1000)
        self.assertIn("熔断减半", "\n".join(half["lines"]))
        for _ in range(2):
            self.close_trade()
        c2 = card(sym="ARB", key="ARB|down|1")
        halted = self.book.handle_alert(alert(c2, ts=NOW), self.plan(c2), NOW, no_price)
        self.assertEqual(halted["cls"], "shadow")
        self.assertIn("熔断", "\n".join(halted["lines"]))

    def test_win_resets_consecutive_stops(self):
        for _ in range(4):
            self.close_trade()
        self.close_trade(loss=False)
        self.assertEqual(paper.risk_state(self.book.state, NOW)["consec"], 0)

    def test_drawdown_over_1_5_percent_halts(self):
        for _ in range(4):
            self.close_trade(pnl=-45.0)      # 4 笔 = -180 > 账户 1.5%（$150）
        self.assertIn("已实现回撤", paper.risk_state(self.book.state, NOW)["reason"])

    def test_five_consecutive_stops_halt_even_when_losses_are_small(self):
        for _ in range(4):
            self.close_trade()
        self.assertIsNone(paper.risk_state(self.book.state, NOW)["reason"])
        self.close_trade()
        self.assertIn("连续 5 笔止损", paper.risk_state(self.book.state, NOW)["reason"])

    def test_profit_giveback_over_30_percent_halts(self):
        self.close_trade(loss=False)         # +158
        self.close_trade(pnl=-60.0)          # 回吐 38%
        self.assertIn("回吐", paper.risk_state(self.book.state, NOW)["reason"])

    def test_halt_resets_baseline_after_three_days(self):
        for _ in range(5):
            self.close_trade()
        self.book.update(NOW, no_price, [])
        self.assertIsNotNone(self.book.state["halt"])
        later = NOW + 4 * DAY
        self.book.update(later, no_price, [])
        self.assertIsNone(self.book.state["halt"])
        self.assertEqual(paper.risk_state(self.book.state, later)["consec"], 0)

    def test_cycle_closes_trades_and_defers_notices_in_quiet_hours(self):
        c = card()
        self.book.handle_alert(alert(c, ts=T0 + DAY // 2), self.plan(c), T0 + DAY // 2, no_price)
        feed = Feed()
        feed.set("SOL", "1d", [kl(datetime.fromtimestamp((T0 + DAY) / 1000, timezone.utc), 100, 109, 99, 108, minutes=1440)])
        sent = []
        self.book.cycle(NOW, feed, sent.append, quiet=True)
        self.assertEqual(self.book.state["trades"][0]["status"], "closed")
        self.assertEqual(sent, [])
        self.assertEqual(len(self.book.state["pending_notices"]), 1)
        self.book.cycle(NOW + 1000, feed, sent.append, quiet=False)
        self.assertEqual(len(sent), 1)
        self.assertIn("止盈", sent[0])
        self.assertIn("+4.00R", sent[0])

    def test_forward_moves_recorded_for_info_alerts(self):
        c = card()
        item = alert(c, "dayup", "level", ts=T0 + DAY // 2)
        feed = Feed()
        feed.set("SOL", "15m", [kl(datetime.fromtimestamp((T0 + DAY // 2) / 1000, timezone.utc), 100, 100, 100, 100)])
        self.book.handle_alert(item, self.plan(c), T0 + DAY // 2, feed)
        rows = [kl(datetime.fromtimestamp((T0 + i * DAY) / 1000, timezone.utc), 100, 101, 99, 100 + 2 * i, minutes=1440) for i in range(6)]
        feed.set("SOL", "1d", rows)
        self.book.update(T0 + 5 * DAY, feed, [])
        fwd = self.book.state["alerts"][0]["fwd"]
        self.assertEqual(fwd["d1"], 2.0)   # 提醒后 24 小时之后收盘的第一根线
        self.assertEqual(fwd["d3"], 6.0)

    def test_review_text_and_files(self):
        win = self.close_trade(loss=False)
        loss = self.close_trade()
        c = card(qualified=False, blocks=["价格在MA200下方：逆势，不抄底"], sym="ARB", key="ARB|down|1")
        self.book.handle_alert(alert(c, ts=NOW - DAY), self.plan(c), NOW - DAY, no_price)
        text = self.book.review_text(NOW)
        for needle in ("纸面交易复盘", "已实现", "真机会", "假机会", "影子单", "闸门检验", "样本太少"):
            self.assertIn(needle, text)
        self.assertLess(len(text), 3500)
        files = dict(self.book.review_files(NOW, "20261114"))
        self.assertIn("paper-journal-20261114.json", files)
        csv_text = files["paper-trades-20261114.csv"].decode("utf-8-sig")
        self.assertIn("纸面", csv_text)
        self.assertIn("影子", csv_text)
        self.assertEqual(win["pnl_usd"], 158.0)
        self.assertEqual(loss["r"], -1.0)

    def test_weekly_review_sent_once_on_sunday_evening_and_on_request(self):
        sent, files = [], []
        sunday = int(datetime(2026, 11, 15, 21, 30, tzinfo=BJ).timestamp() * 1000)
        self.book.cycle(sunday, no_price, sent.append, False, 0, lambda *a: files.append(a[0]))
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(files), 2)
        self.book.cycle(sunday + 900000, no_price, sent.append, False, 0, lambda *a: files.append(a[0]))
        self.assertEqual(len(sent), 1)       # 同一周不重复
        monday = sunday + DAY
        self.book.cycle(monday, no_price, sent.append, False, 0)
        self.assertEqual(len(sent), 1)
        self.book.cycle(monday + 1000, no_price, sent.append, False, monday - 5000)   # /paper 请求
        self.assertEqual(len(sent), 2)
        self.book.cycle(monday + 2000, no_price, sent.append, False, monday - 5000)   # 同一个请求只发一次
        self.assertEqual(len(sent), 2)


class AutowatchIntegrationTest(unittest.TestCase):
    def test_alert_message_carries_card_and_journal_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            book = paper.Paper(Path(folder) / "p.json")
            c = card()
            now = int(datetime(2026, 11, 9, 12, 0, tzinfo=BJ).timestamp() * 1000)
            plan = {"built_at": now - 3600000, "expires_at": now + 86400000, "snapshot_at": now - 3600000, "coins": {"SOL": {"rearm_pct": 0.5}},
                    "cards": {c["key"]: c},
                    "rules": [{"key": c["key"], "sym": "SOL", "kind": "down", "value": 100.0, "src": "level", "tag": "日线支撑位（4段接触，分析时距现价-5.00%）"}]}
            feed = Feed()
            start = datetime.fromtimestamp((now - 30 * 60000) / 1000, timezone.utc)
            feed.set("SOL", "15m", [kl(start + timedelta(minutes=15 * i), 101, 101.5, 99.8, 100.6) for i in range(3)])
            sent = []
            state = autowatch.new_state()
            summary = autowatch.run_auto_cycle(plan, state, sent.append, now_ms=now, getter=feed, settings={"quiet": None}, paper=book)
            self.assertEqual(summary["sent"], 1, summary)
            self.assertIn("纸面单 #", sent[0])
            self.assertIn("进场 100", sent[0])
            self.assertEqual(len(book.state["trades"]), 1)

    def test_paper_failure_does_not_block_alert(self):
        class Broken:
            def handle_alert(self, *args, **kwargs):
                raise RuntimeError("boom")
        c = card()
        now = int(datetime(2026, 11, 9, 12, 0, tzinfo=BJ).timestamp() * 1000)
        plan = {"built_at": now - 3600000, "expires_at": now + 86400000, "coins": {"SOL": {}}, "cards": {c["key"]: c},
                "rules": [{"key": c["key"], "sym": "SOL", "kind": "down", "value": 100.0, "src": "level", "tag": "t"}]}
        feed = Feed()
        start = datetime.fromtimestamp((now - 30 * 60000) / 1000, timezone.utc)
        feed.set("SOL", "15m", [kl(start + timedelta(minutes=15 * i), 101, 101.5, 99.8, 100.6) for i in range(3)])
        sent = []
        summary = autowatch.run_auto_cycle(plan, autowatch.new_state(), sent.append, now_ms=now, getter=feed, settings={"quiet": None}, paper=Broken())
        self.assertEqual(summary["sent"], 1)
        self.assertTrue(any("纸面账本" in e for e in summary["errors"]))


if __name__ == "__main__":
    unittest.main()
