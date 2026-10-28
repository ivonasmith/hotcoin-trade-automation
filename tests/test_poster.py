import copy
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poster"))
import build_data as bd  # noqa: E402
import render  # noqa: E402

BODY = "今天我挂一张$BTC远档空单。进场限价90,000.50，距现价+5.88%；止损91,800.51，按日线收盘判断；目标82,800.48，盈亏比1:4，0.12仓，有效期到11月5日。"
EN = "Today I'm placing a far-away $BTC short limit order. Entry limit 90,000.50, +5.88% from spot; stop 91,800.51 on a daily close; target 82,800.48, reward-to-risk 1:4, 0.12 size, valid until Nov 5."
CLOSED = [{"issue": str(i), "symbol": s, "pct": p, "usd": u} for i, s, p, u in
          [(17, "ETH", "+7.69%", "+50.8 USD"), (9, "XRP", "+9.07%", "+181.4 USD"), (16, "SOL", "+21.46%", "+141.6 USD"),
           (17, "XRP", "-6.16%", "-40.7 USD"), (15, "XRP", "-2.88%", "-19.0 USD"), (15, "BNB", "-2.25%", "-14.8 USD")]]


def payload():
    return {
        "day": "2026-10-21", "issue": 38,
        "formal": {"zh": BODY, "en": EN},
        "order": {"symbol": "BTC", "entry": 90000.5, "stop": 91800.51, "target": 82800.48, "size": 0.1167},
        "prices": {"BTC": 85000.0, "ETH": 2468.2, "SOL": 140.0},
        "ledger": {"active": [
            {"issue": "35", "day": "2026-10-16", "symbol": "ETH", "tier": "慢档日线", "side": "做空", "size": 0.12,
             "entry": 2585.57, "stop": 2637.28, "target": 2378.72, "status": "挂单中"},
            {"issue": "36", "day": "2026-10-19", "symbol": "SOL", "tier": "慢档日线", "side": "做空", "size": 0.33,
             "entry": 150.0, "stop": 155.0, "target": 130.0, "status": "持仓中"}],
            "counts": {"closed": 17}, "equity_usd": 10683.72, "cumulative_pnl_usd": 683.72, "closed_recent": CLOSED,
            "updated_to": "2026-10-16", "updated_issue": 35},
        "facts": {"leg": {"day": 112, "start": "2026-06-30", "start_close": 58624.71, "peak_pct": 47.8,
                          "peak_note_zh": "09-21最高收盘86,620，未刷新", "peak_note_en": "Sep 21 peak close 86,620, not exceeded",
                          "dd": 6.92, "dd_date": "09-15"},
                  "alt": {"day": 55, "d0": "08-27", "d0_close": "80,250", "cleared": "09-26",
                          "top3": "ZEC+207%、ARB+194%、RAY+185%", "top3_en": "ZEC +207%, ARB +194%, RAY +185%"}}}


class BuildDataTest(unittest.TestCase):
    def test_fields_come_from_ledger_order_and_facts(self):
        data, rows, closed = bd.build(payload())
        self.assertEqual((data["issue"], data["date"], data["leg_day"], data["leg_past"], data["alt_day"]), ("38", "2026-10-21", 112, 23, 55))
        self.assertEqual((data["acct_cum"], data["acct_cur"], data["acct_pct"], data["acct_closed"]), ("+$683.72", "$10,683.72", "+6.84%", 17))
        self.assertEqual((data["acct_open"], data["acct_pending"]), (1, 2))
        self.assertEqual(data["badge_today_zh"], "今日新增1单")
        self.assertEqual(data["today_zh"], BODY)
        self.assertEqual(data["today_en"], EN)
        self.assertEqual([r["sym"] for r in rows], ["SOL", "ETH", "BTC"], "持仓中在最前，其余按距现价")
        btc = rows[2]
        self.assertEqual((btc["entry"], btc["stop"], btc["target"]), ("90,000.50", "91,800.51", "82,800.48"))
        self.assertEqual((btc["stop_sub"], btc["target_sub"], btc["pnl_sub_zh"]), ("+2.00%", "-8.00%", "距现价 +5.88%"))
        self.assertEqual(btc["size"], "0.12仓")
        self.assertEqual(len(closed), 6)

    def test_held_position_shows_the_moved_stop_not_the_published_one(self):
        p = payload()
        p["ledger"]["active"][1]["stop_now"] = 150.0          # 2R reached: stop moved to entry
        p["ledger"]["active"][0]["stop_now"] = 2500.0         # pending order: a moved stop makes no sense, ignored
        _, rows, _ = bd.build(p)
        sol, eth = rows[0], rows[1]
        self.assertEqual((sol["sym"], sol["stop"], sol["stop_sub"], sol["stop_moved"]), ("SOL", "150", "+0.00%", True))
        self.assertEqual((eth["sym"], eth["stop"], eth["stop_moved"]), ("ETH", "2,637.28", False))
        p["ledger"]["active"][1]["stop_now"] = 145.0          # 3R: below entry, locked profit
        _, rows, _ = bd.build(p)
        self.assertEqual((rows[0]["stop"], rows[0]["stop_sub"]), ("145", "-3.33%"))
        p["ledger"]["active"][1]["stop_now"] = None
        _, rows, _ = bd.build(p)
        self.assertEqual((rows[0]["stop"], rows[0]["stop_moved"]), ("155", False))

    def test_held_short_floating_pnl_uses_short_formula_and_colours(self):
        _, rows, _ = bd.build(payload())
        self.assertIn("+6.67%", rows[0]["pnl_zh"])
        self.assertIn("var(--green)", rows[0]["pnl_zh"])
        p = payload()
        p["prices"]["SOL"] = 160.0
        _, rows, _ = bd.build(p)
        self.assertIn("-6.67%", rows[0]["pnl_zh"])
        self.assertIn("var(--red)", rows[0]["pnl_zh"])

    def test_pending_row_with_unwritten_fill_is_shown_as_held(self):
        p = payload()
        p["prices"]["ETH"] = 2600.0          # price already through the 2585.57 entry
        p["ledger"]["fills"] = [{"issue": "35", "symbol": "ETH"}]
        _, rows, _ = bd.build(p)
        eth = next(r for r in rows if r["sym"] == "ETH")
        self.assertEqual(eth["state"], "持仓中")
        self.assertNotIn("未成交", eth["pnl_zh"])
        _, rows, _ = bd.build(payload())     # no fill proposal: still pending
        self.assertEqual(next(r for r in rows if r["sym"] == "ETH")["state"], "挂单中")

    def test_order_already_in_ledger_is_not_duplicated(self):
        p = payload()
        p["issue"] = 35
        p["order"] = {"symbol": "ETH", "entry": 2585.57, "stop": 2637.28, "target": 2378.72, "size": 0.12}
        _, rows, _ = bd.build(p)
        self.assertEqual([r["sym"] for r in rows], ["SOL", "ETH"])

    def test_no_new_order_day(self):
        p = payload()
        p["order"] = None
        data, rows, _ = bd.build(p)
        self.assertEqual(data["badge_today_zh"], "今日无新单")
        self.assertEqual(len(rows), 2)

    def test_missing_inputs_refuse_to_draw(self):
        for mutate in (lambda p: p["facts"].pop("leg"), lambda p: p["ledger"].update(cumulative_pnl_usd=None),
                       lambda p: p["ledger"].update(closed_recent=CLOSED[:3])):
            p = payload()
            mutate(p)
            with self.assertRaises(ValueError):
                bd.build(p)

    def test_price_formatting_matches_the_worker(self):
        self.assertEqual([bd.fmt_price(v) for v in (2585.57, 97.8334, 0.088303, 74300, 0.004551)],
                         ["2,585.57", "97.8334", "0.0883", "74,300.00", "0.004551"])

    def test_html_self_check(self):
        htmls = render.html_set(payload())
        self.assertEqual(len(htmls), 4)
        en = htmls[("en", "desktop")]
        self.assertNotRegex(bd._text(en), bd.CJK)
        for html in htmls.values():
            for number in ("90,000.50", "91,800.51", "82,800.48"):
                self.assertIn(number, html)
        bad = payload()
        bad["formal"]["en"] = EN + " 好"
        with self.assertRaises(ValueError) as caught:
            render.html_set(bad)
        self.assertIn("中文字符", str(caught.exception))

    def test_generator_is_untouched_by_the_adapter(self):
        import poster_gen
        self.assertEqual(poster_gen.FOOTER_ZH, "挂单15天有效，接不到就作废｜已发布方向/进场/目标永不回头改，止损只上移不下调｜总仓位不超账户2倍，最多挂5张｜不构成投资建议")
        self.assertEqual(poster_gen.SIGNATURE_EN, "Onchain Uncle | Liquidity to Alpha")
        html = render.html_set(payload())[("zh", "desktop")]
        self.assertIn(poster_gen.FOOTER_ZH, html)


def png_size(path):
    with open(path, "rb") as handle:
        head = handle.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


CHROMIUM = os.environ.get("CHROMIUM_PATH") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "")


@unittest.skipUnless(CHROMIUM, "no Chromium available")
class RenderTest(unittest.TestCase):
    def test_four_pngs_with_standard_names_and_sizes(self):
        os.environ["CHROMIUM_PATH"] = CHROMIUM
        with tempfile.TemporaryDirectory() as out:
            files = render.render(payload(), out)
            self.assertEqual(sorted(files), sorted(["poster_zh_第38期.png", "poster_zh_第38期-手机版.png",
                                                    "poster_en_第38期.png", "poster_en_第38期-手机版.png"]))
            for name, path in files.items():
                width, height = png_size(path)
                self.assertEqual(width, 2160 if "手机版" in name else 4800, name)
                self.assertGreater(height, 1500, name)
                self.assertLess(path.stat().st_size, 5 * 1024 * 1024, "X的图片上限5MB")


if __name__ == "__main__":
    unittest.main()
