import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poster"))
import weekly_pack as wp  # noqa: E402
import weekend_gen as wg  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "weekly"


def load(cal_file="cal_2026-10-25.json"):
    cal = json.loads((FIX / cal_file).read_text(encoding="utf-8"))
    L = json.loads((FIX / "ledger_2026-10-25.json").read_text(encoding="utf-8"))
    notes = {tuple(k.split("|")): v for k, v in L["notes"].items()}
    return cal, L, notes


def pack(cal=None):
    c, L, notes = load()
    return wp.build_pack("2026-10-25", cal or c, L["ledger"], L["log"], notes, L["prices"], L["px_date"])


class WeeklyPack(unittest.TestCase):
    def test_window(self):
        self.assertEqual(wp.week_window("2026-10-25"), ("2026-10-26", "2026-11-01"))
        self.assertEqual(wp.week_window("2026-10-26"), ("2026-11-02", "2026-11-08"))

    def test_events_and_account(self):
        p = pack()
        self.assertEqual([e["title_zh"] for e in p["events"]], ["美联储议息决议"])
        self.assertEqual(p["events"][0]["close_bj"], "2026-10-29 08:00")   # 事件当天日线收盘 = 北京时间次日 08:00 前的那根
        self.assertAlmostEqual(p["account"]["ret_pct"], 6.8372, places=3)
        self.assertEqual(p["account"]["closed_n"], 17)

    def test_positions_use_notes_notional_and_original_stop(self):
        p = pack()
        sol = next(x for x in p["positions"] if x["sym"] == "SOL")
        self.assertEqual(sol["notional"], 233.0)
        self.assertAlmostEqual(sol["r"], 3.09, places=2)
        xrp = next(x for x in p["positions"] if x["sym"] == "XRP")
        self.assertAlmostEqual(xrp["r"], 3.74, places=2)
        self.assertLess(xrp["to_target_pct"], 1)
        self.assertEqual([x["sym"] for x in p["pending"]], ["BTC"])

    def test_moved_stop_gives_no_r(self):
        c, L, notes = load()
        L["ledger"]["active"][0]["stop"] = 105.61      # 止损被改到进场价之下
        p = wp.build_pack("2026-10-25", c, L["ledger"], L["log"], notes, L["prices"], L["px_date"])
        self.assertIsNone(next(x for x in p["positions"] if x["sym"] == "SOL")["r"])

    def test_recap_from_log(self):
        r = pack()["recap"]
        self.assertEqual([n["sym"] for n in r["new"]], ["SOL", "XRP", "BTC"])      # ETH 10-16 不在本周
        self.assertEqual(r["fills"], ["SOL", "XRP"])
        self.assertEqual(r["cancels"][0]["why"], "funding")
        self.assertEqual(r["closes"], [])

    def test_conferences_pick_major_only_and_merge(self):
        p = pack()
        names = [c["en"] for c in p["conferences"]]
        for want in ("Swell (Ripple)", "MERGE Madrid", "Zcon (Zcash)", "Meridian", "LABITCONF", "BTC Balkans", "India Blockchain Week"):
            self.assertIn(want, names)
        self.assertEqual(len([n for n in names if n.startswith("Swell")]), 1)
        self.assertNotIn("Devcon (Ethereum)", names)      # 只有边会，没有主会
        hack = [h["en"] for h in p["hackathons"]]
        self.assertTrue({"Arbitrum", "Solana", "NEAR"} <= set(hack))

    def test_unlocks_listed_when_present_and_flagged_when_absent(self):
        p = pack()
        self.assertTrue(p["unlocks"]["available"])
        self.assertEqual([u["sym"] for u in p["unlocks"]["items"]], ["FF", "SUI"])
        c, _, _ = load()
        c.pop("unlocks")
        q = pack(c)
        self.assertFalse(q["unlocks"]["available"])
        self.assertTrue(any("解锁" in w for w in q["warnings"]))
        zh, en = wp.compose_posts(q)
        self.assertNotIn("币种事件", zh)               # 没数据就不提，不能让读者以为没有解锁

    def test_posts(self):
        zh, en = wp.compose_posts(pack())
        self.assertTrue(zh.startswith("【$BTC下周清单】"))
        self.assertTrue(en.startswith("【$BTC Week Ahead】"))
        self.assertFalse(re.search(r"[一-鿿]", en))
        self.assertNotIn("http", zh + en)
        for text in (zh, en):
            self.assertLessEqual(len(re.findall(r"\$[A-Za-z]", text)), 1)       # X 只允许一个 cashtag
        self.assertIn("+6.84%", zh)
        self.assertIn("+6.84%", en)
        self.assertIn("02:00", zh)
        self.assertIn("没有新平仓", zh)
        self.assertNotIn("名义", zh)

    def test_no_event_week(self):
        c, _, _ = load()
        c["btc"]["events"] = []
        zh, _ = wp.compose_posts(pack(c))
        self.assertIn("不用为事件特意改动作", zh)
        v = wp.view(pack(c), "zh")
        self.assertEqual(v["t1v"], "无")

    def test_posters_self_check_and_html(self):
        p = pack()
        htmls = {l: wg.build(p, l) for l in ("zh", "en")}
        self.assertEqual(wg.check(p, htmls), [])
        self.assertIn("+6.84%", htmls["zh"])
        self.assertNotIn("名义账户", htmls["zh"])

    def test_poster_check_catches_chinese_in_english(self):
        p = pack()
        htmls = {l: wg.build(p, l) for l in ("zh", "en")}
        htmls["en"] = htmls["en"].replace("Week Ahead", "下周", 1)
        self.assertIn("英文版出现中文字符", wg.check(p, htmls))


if __name__ == "__main__":
    unittest.main()
