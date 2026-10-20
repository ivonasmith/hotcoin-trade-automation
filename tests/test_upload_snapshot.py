import contextlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "upload_snapshot", Path(__file__).resolve().parents[1] / "pipeline" / "upload_snapshot.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SECRET = "test-secret-value"


def make_runtime(root):
    attempt = Path(root) / "collection-1"
    attempt.mkdir(parents=True)
    (attempt / "report.json").write_text(json.dumps({"ok": True, "finished_at": "2026-10-20T11:30:00+00:00"}))
    (attempt / "每日汇总.json").write_text(json.dumps({"指标": {s: {"price": 1} for s in ["BTC", "ETH", "SOL", "XRP", "BNB"]}}))
    (attempt / "送审稿四行.txt").write_text("a\nb\nc\nd")
    return str(root)


class Handler(BaseHTTPRequestHandler):
    script = []  # list of (status, body); last one repeats
    seen = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        Handler.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                             "ua": self.headers.get("User-Agent"), "body": json.loads(body)})
        status, text = Handler.script[min(len(Handler.seen) - 1, len(Handler.script) - 1)]
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class UploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Handler.seen = []
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = make_runtime(self.tmp.name)
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, path="/pipeline/snapshot", secret=SECRET, url=None):
        url = url if url is not None else f"https://127.0.0.1:{self.port}{path}"
        env = {"WORKER_INGEST_URL": url, "PIPELINE_SECRET": secret}
        out = io.StringIO()
        # The mock server speaks plain HTTP, so swap the https scheme at request time.
        real_post = module.post_once
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(module, "post_once",
                                  lambda u, s, b, timeout=60: real_post(u.replace("https://", "http://", 1), s, b, timeout)), \
                contextlib.redirect_stdout(out):
            code = module.main(self.runtime, sleep=lambda _s: None)
        return code, out.getvalue()

    def test_success_sends_auth_and_user_agent(self):
        Handler.script = [(200, json.dumps({"ok": True, "day": "2026-10-21"}))]
        code, out = self.run_main()
        self.assertEqual(code, 0)
        sent = Handler.seen[0]
        self.assertEqual(sent["path"], "/pipeline/snapshot")
        self.assertEqual(sent["auth"], "Bearer " + SECRET)
        self.assertIn("hotcoin-trade-automation", sent["ua"])
        self.assertEqual(sent["body"]["four_lines"], "a\nb\nc\nd")
        self.assertNotIn(SECRET, out)

    def test_403_reports_status_and_body_without_secret(self):
        Handler.script = [(403, '{"ok":false}')]
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("::error title=快照上传失败::HTTP 403", out)
        self.assertIn('{"ok":false}', out)
        self.assertEqual(len(Handler.seen), 1)  # no retry on 403
        self.assertNotIn(SECRET, out)

    def test_wrong_path_gets_a_clear_hint(self):
        Handler.script = [(404, "not found")]
        code, out = self.run_main(path="/wrong")
        self.assertEqual(code, 1)
        self.assertIn("/pipeline/snapshot", out)

    def test_server_error_is_retried_then_succeeds(self):
        Handler.script = [(503, "busy"), (200, json.dumps({"ok": True, "day": "2026-10-21"}))]
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(Handler.seen), 2)

    def test_worker_rejection_with_200_is_failure(self):
        Handler.script = [(200, json.dumps({"ok": False}))]
        code, out = self.run_main()
        self.assertEqual(code, 1)

    def test_missing_secret_is_named(self):
        code, out = self.run_main(secret="")
        self.assertEqual(code, 1)
        self.assertIn("PIPELINE_SECRET", out)

    def test_no_collection_output(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        self.runtime = empty.name
        Handler.script = [(200, "{}")]
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("没有找到本次取数输出", out)

    def test_message_is_single_line_annotation(self):
        Handler.script = [(500, "line1\nline2\n<html>")]
        code, out = self.run_main()
        self.assertEqual(code, 1)
        error_lines = [l for l in out.splitlines() if l.startswith("::error")]
        self.assertEqual(len(error_lines), 1)


if __name__ == "__main__":
    unittest.main()
