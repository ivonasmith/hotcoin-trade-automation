"""End-to-end rehearsal on this machine: real Worker + real job runner + real Chromium rendering.
Telegram, the model, GitHub and Notion are faked; nothing is posted anywhere (PUBLISH_ENABLED=false)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "poster"))
sys.path.insert(0, str(ROOT / "tests"))
import job_runner as jr  # noqa: E402
import ledger_write as lw  # noqa: E402
from notion_fake import FakeNotion  # noqa: E402

CHROMIUM = os.environ.get("CHROMIUM_PATH") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "")
DAY = "2026-10-21"
BODY = "今天我挂一张$ETH远档空单。进场限价2,585.57，距现价+4.54%；止损2,637.28，按日线收盘判断；目标2,378.72，盈亏比1:4，0.12仓，有效期到10月31日。成交前日线收盘站上50日均线（约2,553.10）则作废。"
ORDER = {"symbol": "ETH", "side": "空", "entry": 2585.57, "stop": 2637.28, "target": 2378.72, "dist_pct": 4.54, "stop_pct": 2.0,
         "ratio": 4, "ratio_text": "1:4", "size": 0.1167, "size_text": "0.12仓", "amount_usd": 233, "expires": "10月31日",
         "cancel_ma50": 2553.1, "r2": 2482.15, "r3": 2430.44, "path": "路A趋势已破"}
CLOSED = [{"issue": str(i), "symbol": s, "pct": p, "usd": u} for i, s, p, u in
          [(17, "ETH", "+7.69%", "+50.8 USD"), (9, "XRP", "+9.07%", "+181.4 USD"), (16, "SOL", "+21.46%", "+141.6 USD"),
           (17, "XRP", "-6.16%", "-40.7 USD"), (15, "XRP", "-2.88%", "-19.0 USD"), (15, "BNB", "-2.25%", "-14.8 USD")]]


def snapshot():
    metrics = {s: {"price": p} for s, p in [("BTC", 88000.0), ("ETH", 2468.2), ("SOL", 140.0), ("XRP", 1.4), ("BNB", 700.0)]}
    return {"day": DAY, "report": {"ok": True, "finished_at": "2026-10-21T01:50:00+00:00"}, "summary": {"指标": metrics},
            "four_lines": "", "ledger": {"ok": True, "updated_to": "2026-10-16", "updated_issue": 35, "equity_usd": 10683.72,
                                         "cumulative_pnl_usd": 683.72, "counts": {"closed": 17, "open": 0, "pending": 0},
                                         "closed_recent": CLOSED, "active": [],
                                         "proposals": [{"type": "expire", "issue": "35", "symbol": "ETH", "text": "过期"}]},
            "poster_facts": {"ok": True, "leg": {"day": 113, "start": "2026-06-30", "start_close": 58624.71, "peak_pct": 47.8,
                                                 "peak_note_zh": "09-21最高收盘86,620，未刷新", "peak_note_en": "Sep 21 peak close 86,620, not exceeded",
                                                 "dd": 6.92, "dd_date": "09-15"},
                             "alt": {"day": 55, "d0": "08-27", "d0_close": "80,250", "cleared": "09-26",
                                     "top3": "ZEC+207%", "top3_en": "ZEC +207%"}}}


@unittest.skipUnless(CHROMIUM, "no Chromium available")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen(["node", str(ROOT / "tests" / "e2e_server.mjs")], stdout=subprocess.PIPE, text=True, cwd=ROOT)
        line = cls.proc.stdout.readline()
        assert line.startswith("PORT "), line
        cls.base = f"http://127.0.0.1:{line.split()[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)

    def call(self, path, body=None, headers=None, method=None):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                     headers=headers or {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise AssertionError(f"{path} -> {exc.code} {exc.read()[:200]}")

    def state(self):
        return self.call("/__test/log")

    def press(self, cid, data):
        return self.call("/telegram/webhook", {"update_id": 1, "callback_query": {"id": cid, "from": {"id": 123},
                         "message": {"chat": {"id": 123}}, "data": data}}, {"X-Telegram-Bot-Api-Secret-Token": "hook", "Content-Type": "application/json"})

    def run_runner(self, notion=None):
        env = {"PIPELINE_SECRET": "pipe", "WORKER_INGEST_URL": self.base + "/pipeline/snapshot", "NOTION_TOKEN": "tok" if notion else ""}
        with mock.patch.dict(os.environ, {"CHROMIUM_PATH": CHROMIUM}):
            if notion:
                with mock.patch.object(lw, "notion_request", lambda token: notion.call):
                    return jr.main([], environ=env)
            return jr.main([], environ=env)

    def test_full_rehearsal_without_posting(self):
        self.call("/__test/clock?iso=" + "2026-10-21T10:00:00%2B08:00")
        self.call("/pipeline/snapshot", snapshot(), {"Authorization": "Bearer pipe", "Content-Type": "application/json"})
        self.call("/__test/seed", {"draft:" + DAY: {"status": "pending_text_review", "content": f"审核正文：{BODY}\n\n送审备注：可发",
                                                    "order": ORDER, "order_allowed": True, "model": "m"}})
        # 1. text approved (tapped twice) -> formal zh/en + poster job
        self.press("a", f"text_ok:{DAY}")
        self.press("b", f"text_ok:{DAY}")
        st = self.state()
        self.assertEqual(len(st["log"]["model"]), 1, "只翻译一次")
        flow = st["entries"]["flow:" + DAY]
        self.assertEqual((flow["status"], flow["issue"]), ("poster_pending", 38))
        # 2. the poll workflow: peek, then render the real posters and upload them
        self.assertEqual(self.run_runner(), 0)
        st = self.state()
        names = sorted(d["name"] for d in st["log"]["docs"])
        self.assertEqual(names, sorted(["poster_zh_第38期.png", "poster_zh_第38期-手机版.png", "poster_en_第38期.png", "poster_en_第38期-手机版.png"]))
        self.assertTrue(all(d["size"] > 50000 for d in st["log"]["docs"]))
        messages = [m for m in st["log"]["telegram"] if m["method"] == "sendMessage"]
        self.assertTrue(any("中文正式稿" in m["text"] and "交易记录：https://notion.example/ledger" in m["text"] for m in messages))
        self.assertTrue(any("英文正式稿" in m["text"] and "Trade log:" in m["text"] for m in messages))
        self.assertIn("图片与正式稿通过", json.dumps(messages[-1]["reply_markup"], ensure_ascii=False))
        self.assertEqual(st["entries"]["flow:" + DAY]["status"], "awaiting_final")
        # 3. final approval (tapped twice) -> ledger job + schedule
        self.press("c", f"final_ok:{DAY}:1")
        self.press("d", f"final_ok:{DAY}:1")
        st = self.state()
        self.assertEqual(st["alarm"], Date_ms("2026-10-21T20:00:00+08:00"))
        notion = FakeNotion()
        self.run_runner(notion)
        texts = notion.texts(notion.table_by_title("二、"))
        self.assertEqual(texts[-1][:3], ["38", DAY, "ETH"])
        self.assertEqual(len(notion.texts(notion.table_by_title("四、"))), 6)
        self.assertEqual(notion.texts(notion.table_by_title("六、"))[1][3], "过期作废")
        self.run_runner(notion)  # nothing left to do; the ledger is not written twice
        self.assertEqual(len(notion.texts(notion.table_by_title("二、"))), 4)
        # 4. 20:00 and 21:00: rehearsal only
        self.call("/__test/clock?iso=2026-10-21T20:00:20%2B08:00")
        self.call("/__test/alarm"); self.call("/__test/alarm")
        self.call("/__test/clock?iso=2026-10-21T21:00:20%2B08:00")
        self.call("/__test/alarm")
        st = self.state()
        slots = st["entries"]["flow:" + DAY]["slots"]
        self.assertEqual((slots["zh"]["status"], slots["en"]["status"]), ("dry_run", "dry_run"))
        self.assertEqual(st["log"]["x"], [], "没有任何对X的请求")
        drills = [m["text"] for m in st["log"]["telegram"] if m["method"] == "sendMessage" and "【演练，未发推】" in m["text"]]
        self.assertEqual(len(drills), 2)
        self.assertIn("今天我挂一张$ETH远档空单", drills[0])
        self.assertIn("Entry limit 2,585.57", drills[1])
        self.assertEqual(st["alarm"], None)


def Date_ms(iso):
    from datetime import datetime
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


if __name__ == "__main__":
    unittest.main()
