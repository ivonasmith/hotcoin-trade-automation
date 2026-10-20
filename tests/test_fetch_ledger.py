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
