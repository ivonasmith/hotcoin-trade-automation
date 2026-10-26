import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "fetch_ledger", Path(__file__).resolve().parents[1] / "pipeline" / "fetch_ledger.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def rich(text):
    return [{"plain_text": text}]


def heading(text):
    return {"id": "h", "type": "heading_2", "heading_2": {"rich_text": rich(text)}}


def table(table_id):
    return {"id": table_id, "type": "table", "table": {}}


def row(*cells):
    return {"type": "table_row", "table_row": {"cells": [rich(c) for c in cells]}}


TABLES = {
    "t1": [row("项目", "数值"), row("当前账户资金", "10,683.72 美元"), row("累计盈亏", "+683.72 美元"),
           row("更新至", "第 35 期 · 2026-10-16（新增ETH做空限价挂单1张）")],
    "t2": [row("期号", "发布日", "标的", "档位", "方向", "仓位", "进场", "止损（收盘）", "目标", "状态", "备注"),
           row("35", "2026-10-16", "ETH", "慢档日线", "做空", "0.12", "2585.57", "2637.28", "2378.72", "挂单中", "很长的备注"),
           row("30", "2026-10-01", "SOL", "慢档日线", "做多", "0.33", "97.83", "119.78", "107.07", "已了结", "x")],
    "t3": [row("期号"), row("1")],
}
TOP = [heading("一、汇总"), table("t1"), heading("二、当前在场"), table("t2"), heading("三、已结束"), table("t3")]


def fake_get(path, token):
    assert token == "secret-token"
    block_id = path.split("/")[2].split("?")[0]
    if block_id == "3cfb72fc8cae808d9deecfdc21f4cba5":
        return {"results": TOP, "has_more": False}
    return {"results": TABLES[block_id], "has_more": False}


class LedgerTests(unittest.TestCase):
    def test_page_id_from_notion_site_url(self):
        self.assertEqual(module.page_id(module.DEFAULT_LEDGER_URL), "3cfb72fc8cae808d9deecfdc21f4cba5")

    def test_parses_summary_and_only_active_rows_without_notes(self):
        ledger = module.fetch_ledger(module.DEFAULT_LEDGER_URL, "secret-token", fake_get)
        self.assertTrue(ledger["ok"])
        self.assertEqual(ledger["equity_usd"], 10683.72)
        self.assertEqual(ledger["updated_to"], "2026-10-16")
        self.assertEqual(len(ledger["active"]), 1)
        eth = ledger["active"][0]
        self.assertEqual((eth["symbol"], eth["side"], eth["status"], eth["size"], eth["entry"]),
                         ("ETH", "做空", "挂单中", 0.12, 2585.57))
        self.assertNotIn("备注", json.dumps(ledger, ensure_ascii=False))

    def run_main(self, token, get=fake_get):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"NOTION_TOKEN": token, "NOTION_API_KEY": ""}, clear=False):
            code = module.main(tmp, get)
            return code, json.loads((Path(tmp) / "ledger.json").read_text())

    def test_missing_token_does_not_fail_and_marks_unavailable(self):
        code, ledger = self.run_main("")
        self.assertEqual(code, 0)
        self.assertFalse(ledger["ok"])
        self.assertIn("NOTION_TOKEN", ledger["error"])

    def test_api_error_does_not_fail_and_hides_token(self):
        def boom(path, token):
            raise RuntimeError("Notion HTTP 404 找不到页面 secret-token")
        code, ledger = self.run_main("secret-token", boom)
        self.assertEqual(code, 0)
        self.assertFalse(ledger["ok"])

    def test_success_writes_ledger(self):
        code, ledger = self.run_main("secret-token")
        self.assertEqual(code, 0)
        self.assertTrue(ledger["ok"])


if __name__ == "__main__":
    unittest.main()


class ClosedStatsTests(unittest.TestCase):
    HEAD = ["期号", "发布日", "标的", "档位", "方向", "仓位", "进场", "止损", "目标", "状态", "成交日", "平仓价", "币价盈亏", "账户盈亏", "备注"]

    def trade(self, issue, symbol, status, usd):
        return [issue, "d", symbol, "", "", "", "", "", "", status, "", "", "", usd, ""]

    def test_wins_losses_and_worst_loss(self):
        table = [self.HEAD, self.trade("17", "ETH", "已了结", "+50.8 美元"), self.trade("5", "PUMP", "已了结", "-116.8 美元"),
                 self.trade("1", "BTW", "已了结", "-235.6 美元"), self.trade("32", "DOGE", "公开作废", "—")]
        stats = module.build_closed_stats(table, [], 3)
        self.assertEqual(stats, {"wins": 1, "losses": 2, "worst_loss_usd": -235.6})

    def test_log_closes_are_added_without_double_counting(self):
        table = [self.HEAD, self.trade("17", "ETH", "已了结", "+50.8 美元")]
        log = [{"变更": "止损了结", "期号": "17", "标的": "ETH", "账户盈亏": "+50.8 美元"},
               {"变更": "止损了结", "期号": "36", "标的": "SOL", "账户盈亏": "-5.4 美元"}]
        self.assertEqual(module.build_closed_stats(table, log, 2), {"wins": 1, "losses": 1, "worst_loss_usd": -5.4})

    def test_count_mismatch_gives_nothing_instead_of_a_guess(self):
        table = [self.HEAD, self.trade("17", "ETH", "已了结", "+50.8 美元")]
        self.assertIsNone(module.build_closed_stats(table, [], 17))
        self.assertIsNone(module.build_closed_stats(table, [], 0))

    def test_moved_stop_comes_only_from_the_log_and_keeps_status(self):
        head = ["期号", "发布日", "标的", "档位", "方向", "仓位", "进场", "止损（收盘）", "目标", "状态"]
        active = [head, ["36", "2026-10-21", "SOL", "慢档日线", "做空", "0.12", "106.54", "109.02", "96.66", "挂单中"],
                  ["37", "2026-10-21", "XRP", "慢档日线", "做空", "0.17", "1.3724", "1.4033", "1.2489", "挂单中"]]
        log_head = module.LOG_HEADER
        log = [log_head,
               ["2026-10-21", "36", "SOL", "成交", "106.54", "—", "—", "x", "k1"],
               ["2026-10-21", "37", "XRP", "成交", "1.3724", "—", "—", "x", "k2"],
               ["2026-10-24", "36", "SOL", "止损上移", "106.54", "—", "—", "2R保本", "k3"],
               ["2026-10-24", "36", "SOL", "止损上移", "105.61", "—", "—", "3R", "k4"]]
        ledger = module.build_ledger({"一、汇总": [["项目", "数值"], ["更新至", "第 39 期 · 2026-10-23"]],
                                      "二、当前在场": active, "六、状态变更记录": log})
        sol, xrp = ledger["active"]
        self.assertEqual((sol["status"], sol["stop"], sol["stop_now"]), ("持仓中", 109.02, 105.61))
        self.assertEqual((xrp["status"], xrp["stop_now"]), ("持仓中", None))

    def test_no_losses(self):
        table = [self.HEAD, self.trade("17", "ETH", "已了结", "+50.8 美元")]
        self.assertEqual(module.build_closed_stats(table, [], 1)["worst_loss_usd"], None)
