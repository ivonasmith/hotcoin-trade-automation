import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
import hotcoin_host as host  # noqa: E402
import monitor  # noqa: E402

BJ = timezone(timedelta(hours=8))


def bj(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=BJ)


def ms(dt):
    return int(dt.timestamp() * 1000)


class ScheduleTest(unittest.TestCase):
    def test_collect_window_and_weekdays(self):
        state = {}
        self.assertFalse(host.due("collect", bj(2026, 11, 6, 16, 59), state))  # 周五 17:00 前
        self.assertTrue(host.due("collect", bj(2026, 11, 6, 17, 0), state))
        self.assertTrue(host.due("collect", bj(2026, 11, 6, 19, 30), state))  # 补跑窗口内
        self.assertFalse(host.due("collect", bj(2026, 11, 6, 19, 31), state))
        self.assertFalse(host.due("collect", bj(2026, 11, 7, 17, 5), state))  # 周六不跑
        self.assertFalse(host.due("weekly", bj(2026, 11, 6, 15, 5), state))  # 周五不跑周日任务

    def test_weekly_sunday(self):
        self.assertFalse(host.due("weekly", bj(2026, 11, 8, 14, 59), {}))
        self.assertTrue(host.due("weekly", bj(2026, 11, 8, 15, 0), {}))
        self.assertFalse(host.due("weekly", bj(2026, 11, 8, 19, 1), {}))

    def test_done_and_retry_gap(self):
        state = {}
        now = bj(2026, 11, 6, 17, 0)
        host.mark("collect", now, False, state)
        self.assertFalse(host.due("collect", now + timedelta(minutes=5), state))  # 失败后 10 分钟内不重试
        self.assertTrue(host.due("collect", now + timedelta(minutes=10), state))
        host.mark("collect", now + timedelta(minutes=10), True, state)
        self.assertFalse(host.due("collect", now + timedelta(minutes=40), state))  # 当天已完成
        self.assertTrue(host.due("collect", bj(2026, 11, 9, 17, 1), state))  # 下个工作日又该跑

    def test_tick_runs_once_and_records_failure(self):
        calls = []
        state = {}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            tasks = {"collect": lambda env: calls.append("collect"), "weekly": lambda env: calls.append("weekly")}
            ran = host.scheduler_tick({}, state, tasks, bj(2026, 11, 6, 17, 1), state_path=path)
            self.assertEqual(ran, ["collect"])
            self.assertEqual(host.scheduler_tick({}, state, tasks, bj(2026, 11, 6, 17, 2), state_path=path), [])
            self.assertEqual(json.loads(path.read_text())["collect"]["done"], "2026-11-06")

            def boom(env):
                raise RuntimeError("币安拒绝")
            failing = {"collect": boom, "weekly": boom}
            state2 = {}
            host.scheduler_tick({}, state2, failing, bj(2026, 11, 9, 17, 0), state_path=path)
            self.assertIn("币安拒绝", state2["collect"]["error"])
            self.assertNotEqual(state2["collect"].get("done"), "2026-11-09")


class EnvTest(unittest.TestCase):
    def test_parse_env(self):
        values = host.parse_env_text('# 注释\nWORKER_INGEST_URL="https://x.workers.dev/a"\nPIPELINE_SECRET=abc\n\nBAD LINE\n')
        self.assertEqual(values, {"WORKER_INGEST_URL": "https://x.workers.dev/a", "PIPELINE_SECRET": "abc"})

    def test_worker_base_keeps_origin_only(self):
        self.assertEqual(host.worker_base({"WORKER_INGEST_URL": "https://x.workers.dev/pipeline/snapshot"}), "https://x.workers.dev")
        with self.assertRaises(ValueError):
            host.worker_base({"WORKER_INGEST_URL": "http://example.com"})


def bar(open_time_dt, o, h, l, c, minutes=15):
    start = ms(open_time_dt)
    return {"open_time": start, "open": o, "high": h, "low": l, "close": c, "close_time": start + minutes * 60000 - 1}


class MonitorTest(unittest.TestCase):
    def rule(self, **kw):
        base = {"id": 1, "sym": "SOL", "kind": "down", "value": 95, "repeat": False, "created": ms(bj(2026, 11, 6, 9, 0)), "last_fired": None}
        base.update(kw)
        return base

    def test_touch_down_uses_wick_after_creation(self):
        now = ms(bj(2026, 11, 6, 10, 20))
        bars = [bar(bj(2026, 11, 6, 10, 0), 97, 97.5, 94.8, 96)]
        text, _ = monitor.evaluate(self.rule(), bars, now, None)
        self.assertIn("SOL 价格跌到 95", text)
        self.assertIn("94.8", text)

    def test_touch_ignores_bars_before_creation(self):
        now = ms(bj(2026, 11, 6, 10, 20))
        old = [bar(bj(2026, 11, 6, 8, 30), 97, 97.5, 90, 96)]
        self.assertIsNone(monitor.evaluate(self.rule(), old, now, None)[0])

    def test_touch_up(self):
        now = ms(bj(2026, 11, 6, 10, 20))
        bars = [bar(bj(2026, 11, 6, 10, 0), 99, 101, 98.5, 100)]
        text, _ = monitor.evaluate(self.rule(kind="up", value=100.5), bars, now, None)
        self.assertIn("价格涨到 100.5", text)

    def test_cooldown_blocks_repeat(self):
        now = ms(bj(2026, 11, 6, 10, 20))
        bars = [bar(bj(2026, 11, 6, 10, 0), 97, 97.5, 94, 96)]
        rule = self.rule(repeat=True, last_fired=now - 3600 * 1000)
        self.assertIsNone(monitor.evaluate(rule, bars, now, None)[0])
        rule = self.rule(repeat=True, last_fired=now - 7 * 3600 * 1000)
        self.assertIsNotNone(monitor.evaluate(rule, bars, now, None)[0])

    def test_daily_close_only_after_bar_closed_and_once(self):
        created = ms(bj(2026, 11, 5, 12, 0))
        closed = bar(bj(2026, 11, 5, 8, 0), 90, 99, 89, 98.7, minutes=24 * 60)  # 北京 11-05 08:00～11-06 08:00
        forming = bar(bj(2026, 11, 6, 8, 0), 98.7, 120, 98, 119, minutes=24 * 60)
        rule = self.rule(kind="dayup", value=98, created=created)
        now = ms(bj(2026, 11, 6, 12, 0))
        text, bar_open = monitor.evaluate(rule, [closed, forming], now, None)
        self.assertIn("日线收盘站上 98", text)
        self.assertIn("收盘 98.7", text)  # 用已收盘那根的收盘价，不看没收盘的 119
        self.assertEqual(bar_open, closed["open_time"])
        self.assertIsNone(monitor.evaluate(rule, [closed, forming], now, closed["open_time"])[0])  # 同一根只判一次

    def test_close_below_not_triggered_by_forming_bar(self):
        created = ms(bj(2026, 11, 5, 12, 0))
        closed = bar(bj(2026, 11, 5, 8, 0), 90, 99, 89, 98.7, minutes=24 * 60)
        forming = bar(bj(2026, 11, 6, 8, 0), 98.7, 99, 80, 81, minutes=24 * 60)
        rule = self.rule(kind="daydown", value=95, created=created)
        text, _ = monitor.evaluate(rule, [closed, forming], ms(bj(2026, 11, 6, 12, 0)), None)
        self.assertIsNone(text)

    def test_surge_uses_closed_bar_percent(self):
        now = ms(bj(2026, 11, 6, 10, 20))
        bars = [bar(bj(2026, 11, 6, 10, 0), 100, 104, 99.5, 103.4), bar(bj(2026, 11, 6, 10, 15), 103.4, 104, 103, 103.9)]
        text, bar_open = monitor.evaluate(self.rule(kind="surge", value=3), bars, now, None)
        self.assertIn("+3.40%", text)  # 10:00 那根已收盘；10:15 那根 20 分钟时还没收盘
        self.assertEqual(bar_open, bars[0]["open_time"])
        self.assertIsNone(monitor.evaluate(self.rule(kind="surge", value=3), bars, now, bar_open)[0])  # 同一根只判一次
        # 漏跑了一轮：两根都已收盘，中间那根的涨幅也会被看到
        later = ms(bj(2026, 11, 6, 10, 31))
        text, _ = monitor.evaluate(self.rule(kind="surge", value=3), bars, later, None)
        self.assertIn("+3.40%", text)

    def test_plunge(self):
        now = ms(bj(2026, 11, 6, 10, 16))
        bars = [bar(bj(2026, 11, 6, 10, 0), 100, 100.2, 96, 96.5), bar(bj(2026, 11, 6, 10, 15), 96.5, 97, 96, 96.8)]
        text, _ = monitor.evaluate(self.rule(kind="plunge", value=3), bars, now, None)
        self.assertIn("-3.50%", text)


class CycleTest(unittest.TestCase):
    def klines(self, rows):
        return [[b["open_time"], str(b["open"]), str(b["high"]), str(b["low"]), str(b["close"]), "0", b["close_time"]] for b in rows]

    def test_cycle_notifies_and_reports_fired_ids_and_caches_requests(self):
        now_dt = bj(2026, 11, 6, 10, 20)
        rows = self.klines([bar(bj(2026, 11, 6, 10, 0), 97, 97.5, 94, 96)])
        urls = []

        def getter(url):
            urls.append(url)
            return rows
        rules = [
            {"id": 1, "sym": "SOL", "kind": "down", "value": 95, "repeat": False, "created": ms(bj(2026, 11, 6, 9, 0)), "last_fired": None},
            {"id": 2, "sym": "SOL", "kind": "up", "value": 120, "repeat": False, "created": ms(bj(2026, 11, 6, 9, 0)), "last_fired": None},
        ]
        sent = []
        summary = monitor.run_cycle(rules, {}, lambda text, ids: sent.append((text, ids)), ms(now_dt), getter)
        self.assertEqual(len(urls), 1)  # 同一个币同一周期只拉一次
        self.assertIn("fapi.binance.com", urls[0])
        self.assertEqual(sent[0][1], [1])
        self.assertIn("【监控提醒】11-06 10:20", sent[0][0])
        self.assertEqual(summary["fired"], 1)

    def test_fallback_domain_and_1000_prefix(self):
        rows = self.klines([bar(bj(2026, 11, 6, 10, 0), 0.01, 0.011, 0.009, 0.0105)])
        urls = []

        def getter(url):
            urls.append(url)
            if "symbol=PEPEUSDT" in url:
                raise urllib.error.HTTPError(url, 400, "bad", {}, _Body(b'{"code":-1121,"msg":"Invalid symbol."}'))
            if url.startswith("https://fapi.binance.com"):
                raise OSError("blocked")
            return rows
        contract, bars = monitor.fetch_klines("PEPE", "15m", 3, getter)
        self.assertEqual(contract, "1000PEPEUSDT")
        self.assertEqual(len(bars), 1)
        self.assertTrue(any(u.startswith("https://www.binance.com") for u in urls))

    def test_unknown_symbol_warned_once(self):
        def getter(url):
            raise urllib.error.HTTPError(url, 400, "bad", {}, _Body(b'{"code":-1121,"msg":"Invalid symbol."}'))
        rules = [{"id": 5, "sym": "ZZZZ", "kind": "up", "value": 1, "repeat": False, "created": 0, "last_fired": None}]
        sent, state = [], {}
        monitor.run_cycle(rules, state, lambda t, i: sent.append((t, i)), ms(bj(2026, 11, 6, 10, 20)), getter)
        monitor.run_cycle(rules, state, lambda t, i: sent.append((t, i)), ms(bj(2026, 11, 6, 10, 35)), getter)
        self.assertEqual(len(sent), 1)
        self.assertIn("找不到", sent[0][0])
        self.assertEqual(sent[0][1], [])

    def test_network_error_is_reported_not_raised(self):
        def getter(url):
            raise OSError("timeout")
        rules = [{"id": 1, "sym": "SOL", "kind": "up", "value": 1, "repeat": False, "created": 0, "last_fired": None}]
        summary = monitor.run_cycle(rules, {}, lambda t, i: self.fail("不该发消息"), ms(bj(2026, 11, 6, 10, 20)), getter)
        self.assertEqual(summary["fetched"], 0)
        self.assertTrue(summary["errors"])


class SelfUpdateTest(unittest.TestCase):
    def test_restart_only_when_host_or_requirements_change(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origin, clone = tmp / "origin", tmp / "clone"
            run = lambda *a, cwd=None: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)
            run("git", "init", "-q", "-b", "main", str(origin))
            run("git", "config", "user.email", "t@t", cwd=origin)
            run("git", "config", "user.name", "t", cwd=origin)
            (origin / "host").mkdir()
            (origin / "host" / "a.py").write_text("1")
            (origin / "pipeline").mkdir()
            (origin / "pipeline" / "x.py").write_text("1")
            run("git", "add", "-A", cwd=origin)
            run("git", "commit", "-q", "-m", "1", cwd=origin)
            run("git", "clone", "-q", str(origin), str(clone))
            old_root = host.ROOT
            host.ROOT = clone
            try:
                self.assertFalse(host.self_update({}))  # 没有新提交
                (origin / "pipeline" / "x.py").write_text("2")
                run("git", "commit", "-q", "-am", "2", cwd=origin)
                self.assertFalse(host.self_update({}))  # 只改了取数脚本：下次任务前自动拉取，不用重启
                (origin / "host" / "a.py").write_text("2")
                run("git", "commit", "-q", "-am", "3", cwd=origin)
                self.assertTrue(host.self_update({}))  # 主机程序变了：要重启
            finally:
                host.ROOT = old_root


class WorkerCallTest(unittest.TestCase):
    def test_watchlist_notify_heartbeat_over_http(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def respond(self, payload, code=200):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer s3":
                    return self.respond({"ok": False}, 403)
                self.respond({"ok": True, "rules": [{"id": 1, "sym": "SOL", "kind": "up", "value": 100}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, body))
                self.respond({"ok": True})

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {"WORKER_INGEST_URL": f"http://127.0.0.1:{server.server_port}/pipeline/snapshot", "PIPELINE_SECRET": "s3"}
        try:
            self.assertEqual(host.fetch_watchlist(env)[0]["sym"], "SOL")
            host.notify(env, "提醒", [1])
            host.heartbeat(env, {"monitor": "ok"})
            with self.assertRaises(RuntimeError):
                host.fetch_watchlist({**env, "PIPELINE_SECRET": "bad"})
        finally:
            server.shutdown()
        self.assertEqual(seen[0], ("/pipeline/notify", {"text": "提醒", "fired": [1]}))
        self.assertEqual(seen[1][0], "/pipeline/heartbeat")
        self.assertEqual(seen[1][1]["tasks"], {"monitor": "ok"})
        self.assertEqual(seen[1][1]["version"], host.VERSION)


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
