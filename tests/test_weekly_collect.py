import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "poster"))
import weekly_collect as wc  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "weekly"
CAL = json.loads((FIX / "cal_2026-10-25.json").read_text(encoding="utf-8"))
URL = "https://x.notion.site/Ledger-" + "a" * 32


def rich(t):
    return [{"plain_text": t}]


def heading(t):
    return {"id": "h" + t, "type": "heading_2", "heading_2": {"rich_text": rich(t)}}


def tbl(i):
    return {"id": i, "type": "table", "table": {}}


def row(*cells):
    return {"type": "table_row", "table_row": {"cells": [rich(c) for c in cells]}}


H2 = ("期号", "发布日", "标的", "档位", "方向", "仓位", "进场", "止损（收盘）", "目标", "状态", "备注")
TABLES = {
    "t1": [row("项目", "数值"), row("已了结笔数", "17 笔"), row("累计盈亏", "+683.72 美元"), row("当前账户资金", "10,683.72 美元"),
           row("更新至", "第 39 期 · 2026-10-23")],
    "t2": [row(*H2),
           row("36", "2026-10-21", "SOL", "慢档日线", "做空", "0.12", "106.543333", "109.015138", "96.656112", "挂单中", "仓位0.12仓（名义约233美元，止损约亏5.4美元）"),
           row("37", "2026-10-21", "XRP", "慢档日线", "做空", "0.17", "1.3724", "1.403279", "1.248884", "挂单中", "仓位0.17仓（名义约333美元，止损约亏7.5美元）"),
           row("39", "2026-10-23", "BTC", "慢档日线", "做空", "0.17", "82314.42", "83960.7084", "75729.2664", "挂单中", "仓位0.17仓（名义约333美元")],
    "t3": [row(*H2, "成交日", "平仓价", "币价盈亏", "账户盈亏"), ],
    "t6": [row("日期", "期号", "标的", "变更", "价格", "币价盈亏", "账户盈亏", "依据", "记录标记"),
           row("2026-10-21", "36", "SOL", "成交", "106.54", "—", "—", "触及", "m1"),
           row("2026-10-21", "37", "XRP", "成交", "1.3724", "—", "—", "触及", "m2")],
}
TOP = [heading("一、汇总"), tbl("t1"), heading("二、当前在场"), tbl("t2"), heading("三、已结束"), tbl("t3"), heading("六、状态变更记录"), tbl("t6")]


def notion_get(path, token):
    if token != "tok":
        raise RuntimeError("Notion HTTP 401 令牌无效")
    block = path.split("/blocks/")[1].split("/children")[0]
    if block == "a" * 32:
        return {"results": TOP, "has_more": False}
    return {"results": TABLES[block], "has_more": False}


def bars(close, days=("2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25")):
    out = []
    for d in days:
        ts = int(datetime.fromisoformat(d + "T00:00:00+00:00").timestamp() * 1000)
        out.append([ts, 0, 0, 0, close, 0])
    return out


def klines(symbol, interval, n):
    if symbol == "SOLUSDT":
        return bars(98.91)
    if symbol == "XRPUSDT":
        return bars(1.2569)
    raise RuntimeError("no data")


FETCH = lambda url, timeout=30: CAL  # noqa: E731
NOW = datetime(2026, 10, 25, 7, 17, tzinfo=timezone.utc)
ENV = {"NOTION_TOKEN": "tok", "TRADE_LOG_URL": URL}


class Collect(unittest.TestCase):
    def test_latest_closes_skip_forming_bar(self):
        prices, day = wc.latest_closes(["SOL"], klines, NOW)
        self.assertEqual(prices["SOL"]["close"], 98.91)
        self.assertEqual(day, "2026-10-24")        # 10-25 的日线还没收盘，不用

    def test_build_end_to_end(self):
        pack, zh, en = wc.build("2026-10-25", ENV, fetch=FETCH, notion_get=notion_get, klines=klines, now=NOW)
        self.assertEqual(pack["px_date"], "2026-10-24")
        self.assertEqual({p["sym"] for p in pack["positions"]}, {"SOL", "XRP"})
        self.assertEqual([p["sym"] for p in pack["pending"]], ["BTC"])
        self.assertAlmostEqual(pack["account"]["ret_pct"], 6.8372, places=3)
        self.assertEqual([n["sym"] for n in pack["recap"]["new"]], ["SOL", "XRP", "BTC"])
        self.assertTrue(zh.startswith("【$BTC下周清单】") and en.startswith("【$BTC Week Ahead】"))
        self.assertIn("+3.1R", zh)

    def test_calendar_fallback_and_failure(self):
        calls = []

        def flaky(url, timeout=30):
            calls.append(url)
            if "uncleonchain.com" in url:
                raise OSError("down")
            return CAL
        self.assertIs(wc.load_calendar(flaky), CAL)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(RuntimeError):
            wc.load_calendar(lambda url, timeout=30: (_ for _ in ()).throw(OSError("down")))
        with self.assertRaises(RuntimeError):
            wc.load_calendar(lambda url, timeout=30: {"nope": 1})

    def test_ledger_failure_is_a_clear_error_and_hides_token(self):
        with self.assertRaises(RuntimeError) as c:
            wc.build("2026-10-25", {"NOTION_TOKEN": "bad", "TRADE_LOG_URL": URL}, fetch=FETCH, notion_get=notion_get, klines=klines, now=NOW)
        self.assertIn("台账读取失败", str(c.exception))
        with self.assertRaises(RuntimeError) as c:
            wc.build("2026-10-25", {}, fetch=FETCH, notion_get=notion_get, klines=klines, now=NOW)
        self.assertIn("NOTION_TOKEN", str(c.exception))

    def test_no_prices_at_all_is_an_error_but_missing_one_is_a_warning(self):
        with self.assertRaises(RuntimeError):
            wc.build("2026-10-25", ENV, fetch=FETCH, notion_get=notion_get, klines=lambda *a: (_ for _ in ()).throw(RuntimeError("x")), now=NOW)
        only_sol = lambda s, i, n: bars(98.91) if s == "SOLUSDT" else (_ for _ in ()).throw(RuntimeError("x"))  # noqa: E731
        pack, zh, en = wc.build("2026-10-25", ENV, fetch=FETCH, notion_get=notion_get, klines=only_sol, now=NOW)
        self.assertTrue(any("XRP" in w for w in pack["warnings"]))

    def test_weekly_url(self):
        self.assertEqual(wc.weekly_url("https://w.example.dev/pipeline/snapshot"), "https://w.example.dev/pipeline/weekly")
        with self.assertRaises(ValueError):
            wc.weekly_url("http://evil.example/x")

    def test_main_uploads_and_reports_failures(self):
        sent = []

        def post(url, secret, body, timeout=60):
            sent.append((url, secret, body))
            return 200, "{}"
        env = {**ENV, "PIPELINE_SECRET": "s", "WORKER_INGEST_URL": "https://w.example.dev/pipeline/snapshot"}
        rc = wc.main(["--day", "2026-10-25"], env, fetch=FETCH, notion_get=notion_get, klines=klines, now=NOW, post=post)
        self.assertEqual(rc, 0)
        url, secret, body = sent[0]
        self.assertEqual((url, secret), ("https://w.example.dev/pipeline/weekly", "s"))
        self.assertEqual(body["day"], "2026-10-25")
        self.assertEqual(body["pack"]["kind"], "weekly")
        self.assertNotIn("http", body["zh_post"])
        sent.clear()
        rc = wc.main(["--day", "2026-10-25"], {**env, "NOTION_TOKEN": "bad"}, fetch=FETCH, notion_get=notion_get, klines=klines, now=NOW, post=post)
        self.assertEqual(rc, 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("台账读取失败", sent[0][2]["error"])
        self.assertNotIn("bad", sent[0][2]["error"].replace("读取失败", ""))

    def test_main_retries_server_errors_then_fails_loudly(self):
        calls = []

        def post(url, secret, body, timeout=60):
            calls.append(1)
            return 502, "bad gateway"
        env = {**ENV, "PIPELINE_SECRET": "s", "WORKER_INGEST_URL": "https://w.example.dev/pipeline/snapshot"}
        orig = wc.time.sleep
        wc.time.sleep = lambda s: None
        try:
            rc = wc.main(["--day", "2026-10-25"], env, fetch=FETCH, notion_get=notion_get, klines=klines, now=NOW, post=post)
        finally:
            wc.time.sleep = orig
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 3)

    def test_main_out_dir_writes_files_without_upload(self):
        with tempfile.TemporaryDirectory() as d:
            rc = wc.main(["--day", "2026-10-25", "--out", d], ENV, fetch=FETCH, notion_get=notion_get, klines=klines, now=NOW)
            self.assertEqual(rc, 0)
            self.assertTrue((Path(d) / "pack.json").exists() and (Path(d) / "post_zh.txt").exists())

    def test_missing_secrets(self):
        self.assertEqual(wc.main([], {}), 1)


if __name__ == "__main__":
    unittest.main()
