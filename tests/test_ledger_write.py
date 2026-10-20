import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_ledger as fl  # noqa: E402
import ledger_write as lw  # noqa: E402
from notion_fake import FakeNotion  # noqa: E402

URL = "https://example.notion.site/HotCoinDesk-" + FakeNotion.PAGE


def order_payload(**changes):
    payload = {
        "day": "2026-10-21", "issue": 38,
        "order": {"symbol": "BTC", "tier": "慢档日线", "size": 0.12, "entry": 90000.5, "stop": 91800.51, "target": 82800.48},
        "note": "程序按已审核送审稿写入。仓位0.12仓",
        "conditions": [
            {"condition": "成交前BTC日线收盘价≥当日MA50", "action": "本单作废", "triggered": "否", "conclusion": "每期复核"},
            {"condition": "成交前BTC资金费率年化转负", "action": "撤单", "triggered": "否", "conclusion": "每期复核"},
            {"condition": "成交后价格到86400（2R）", "action": "止损下移到进场价", "triggered": "否", "conclusion": "挂单中"}],
        "proposals": []}
    payload.update(changes)
    return payload


def run(fake, payload):
    return lw.apply(payload, call=fake.call, page_url=URL)


def mutations(fake):
    return len(fake.writes)


class LedgerWriteTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNotion()

    def table(self, prefix):
        return self.fake.texts(self.fake.table_by_title(prefix))

    def summary(self):
        return {row[0]: row[1] for row in self.table("一、")}

    def test_new_order_is_appended_with_conditions_and_summary(self):
        before_active = copy.deepcopy(self.table("二、"))
        before_registry = copy.deepcopy(self.table("四、"))
        actions = run(self.fake, order_payload())
        self.assertEqual(len(actions), 3)
        active = self.table("二、")
        self.assertEqual(active[:len(before_active)], before_active, "原记录一字不动")
        row = active[-1]
        self.assertEqual(row[:10], ["38", "2026-10-21", "BTC", "慢档日线", "做空", "0.12", "90000.5", "91800.51", "82800.48", "挂单中"])
        self.assertIn("[desk:2026-10-21:38:BTC:new]", row[14])
        registry = self.table("四、")
        self.assertEqual(registry[:len(before_registry)], before_registry)
        self.assertEqual(len(registry), len(before_registry) + 3)
        summary = self.summary()
        self.assertEqual(summary["挂单未成交"], "2 笔")
        self.assertTrue(summary["更新至"].startswith("第 38 期 · 2026-10-21（新增BTC做空限价挂单1张"))
        self.assertIn("上期记录：第 35 期 · 2026-10-16", summary["更新至"])

    def test_rerun_changes_nothing(self):
        run(self.fake, order_payload())
        snapshot = copy.deepcopy(self.fake.kids), copy.deepcopy(self.fake.blocks)
        count = mutations(self.fake)
        self.assertEqual(run(self.fake, order_payload()), [])
        self.assertEqual(mutations(self.fake), count)
        self.assertEqual((self.fake.kids, self.fake.blocks), snapshot)

    def test_existing_order_condition_backfill_is_append_only_and_idempotent(self):
        before_active = copy.deepcopy(self.table("二、"))
        before_registry = copy.deepcopy(self.table("四、"))
        payload = order_payload(order=None, conditions=[], condition_backfills=[{
            "issue": 38, "symbol": "SOL", "condition": "成交前SOL价格先跌到目标96.6561",
            "action": "本单作废（目标已先到，不再追空）", "triggered": "否（未成交，价格未到目标）", "conclusion": "每期复核"}])
        self.assertEqual(run(self.fake, payload), ["条件单登记簿补写1条"])
        self.assertEqual(self.table("二、"), before_active)
        registry = self.table("四、")
        self.assertEqual(registry[:len(before_registry)], before_registry)
        self.assertEqual(registry[-1], ["38", "SOL", "成交前SOL价格先跌到目标96.6561",
                                        "本单作废（目标已先到，不再追空）", "否（未成交，价格未到目标）", "每期复核"])
        writes = mutations(self.fake)
        self.assertEqual(run(self.fake, payload), [])
        self.assertEqual(mutations(self.fake), writes)

    def test_crash_between_summary_and_row_does_not_double_count(self):
        self.fake.fail_on = lambda m, p, b: m == "PATCH" and p.endswith("/children") and \
            b["children"][0]["table_row"]["cells"][0][0]["text"]["content"] == "38" and len(b["children"][0]["table_row"]["cells"]) == 15
        with self.assertRaises(RuntimeError):
            run(self.fake, order_payload())
        self.assertEqual(self.summary()["挂单未成交"], "2 笔")
        run(self.fake, order_payload())
        self.assertEqual(self.summary()["挂单未成交"], "2 笔", "重试不再加一次")
        self.assertEqual(len(self.table("二、")), 4)
        self.assertEqual(len(self.table("四、")), 5, "条件不重复")

    def test_status_changes_go_to_an_append_only_log(self):
        before_active = copy.deepcopy(self.table("二、"))
        proposals = [{"type": "fill", "issue": "35", "symbol": "ETH", "date": "2026-10-20", "text": "x"},
                     {"type": "review", "text": "不处理"}, {"type": "check", "text": "不处理"}]
        actions = run(self.fake, order_payload(order=None, proposals=proposals))
        self.assertTrue(any("状态变更记录追加1行" in a for a in actions))
        self.assertEqual(self.table("二、"), before_active, "当前在场里的原行不改不删")
        log = self.table("六、")
        self.assertEqual(log[0], fl.LOG_HEADER)
        self.assertEqual([r[3] for r in log[1:]], ["成交"])
        summary = self.summary()
        self.assertEqual(summary["过期未成交"], "6 笔")
        self.assertEqual(summary["挂单未成交"], "0 笔")
        self.assertEqual(summary["持仓中笔数"], "2 笔")
        count = mutations(self.fake)
        self.assertEqual(run(self.fake, order_payload(order=None, proposals=proposals)), [])
        self.assertEqual(mutations(self.fake), count)

    def test_stop_out_books_pnl_from_the_close_price(self):
        proposals = [{"type": "stop_out", "issue": "36", "symbol": "SOL", "date": "2026-10-20", "price": 160.0, "text": "止损"}]
        run(self.fake, order_payload(order=None, proposals=proposals))
        row = self.table("六、")[1]
        self.assertEqual((row[3], row[4], row[5], row[6]), ("止损了结", "160", "-6.67%", "-44.0 美元"))
        summary = self.summary()
        self.assertEqual(summary["累计盈亏"], "+639.72 美元")
        self.assertEqual(summary["当前账户资金"], "10,639.72 美元")
        self.assertEqual(summary["账户收益率"], "+6.40%")
        self.assertEqual((summary["已了结笔数"], summary["持仓中笔数"]), ("18 笔", "0 笔"))

    def test_reader_applies_the_log_to_the_effective_ledger(self):
        proposals = [{"type": "expire", "issue": "35", "symbol": "ETH", "text": "a"},
                     {"type": "stop_out", "issue": "36", "symbol": "SOL", "date": "2026-10-20", "price": 160.0, "text": "b"}]
        run(self.fake, order_payload(proposals=proposals))
        ledger = fl.fetch_ledger(URL, "t", lambda path, token: self.fake.call("GET", path))
        self.assertEqual([r["symbol"] for r in ledger["active"]], ["BTC"], "过期和止损的单子不再算在场")
        self.assertEqual(ledger["counts"], {"closed": 18, "open": 0, "pending": 1})
        self.assertEqual(ledger["updated_issue"], 38)
        self.assertEqual(ledger["updated_to"], "2026-10-21")
        self.assertEqual(ledger["closed_recent"][0], {"issue": "36", "symbol": "SOL", "pct": "-6.67%", "usd": "-44.0 USD"})
        self.assertEqual(len(ledger["closed_recent"]), 6)
        self.assertEqual(ledger["cumulative_pnl_usd"], 639.72)

    def test_wrong_column_count_is_refused_before_writing(self):
        bad = order_payload()
        bad["conditions"][0] = dict(bad["conditions"][0])
        original = lw.condition_rows
        lw.condition_rows = lambda payload: [["38", "BTC", "只有三列"]]
        try:
            with self.assertRaises(RuntimeError):
                run(self.fake, bad)
        finally:
            lw.condition_rows = original
        self.assertEqual(len(self.table("二、")), 3)

    def test_missing_active_table_stops(self):
        fake = FakeNotion()
        bid = fake.table_by_title("二、")
        fake.blocks[bid] = {"id": bid, "type": "paragraph", "paragraph": {"rich_text": []}}
        with self.assertRaises(RuntimeError):
            run(fake, order_payload())


if __name__ == "__main__":
    unittest.main()
