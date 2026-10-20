import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "poster"))
import job_runner as jr  # noqa: E402

BASE = "https://desk.example/pipeline/jobs"


class Recorder:
    def __init__(self, job):
        self.job, self.calls = job, []

    def __call__(self, method, url, secret, body=None, content_type="application/json", timeout=120):
        self.calls.append((method, url, body, content_type))
        if url.endswith("/next"):
            return 200, {"ok": True, "job": self.job}
        if url.endswith("/next?peek=1"):
            return 200, {"ok": True, "job": self.job}
        return 200, {"ok": True}


class JobRunnerTest(unittest.TestCase):
    def test_base_url_only_from_https_ingest_url(self):
        self.assertEqual(jr.jobs_base("https://desk.example/pipeline/snapshot"), BASE)
        with self.assertRaises(ValueError):
            jr.jobs_base("http://desk.example/pipeline/snapshot")

    def test_no_job_means_nothing_to_do(self):
        self.assertFalse(jr.run_one(BASE, "s", "", Recorder(None)))

    def test_poster_job_uploads_each_png_then_completes(self):
        job = {"id": "poster:2026-10-21:1", "type": "poster", "payload": {"issue": 38}}
        rec = Recorder(job)
        with tempfile.TemporaryDirectory() as tmp:
            files = {}
            for name in ("poster_zh_第38期.png", "poster_en_第38期.png"):
                path = Path(tmp) / name
                path.write_bytes(b"\x89PNG" + b"0" * 10)
                files[name] = path
            fake = mock.Mock()
            fake.render.return_value = files
            with mock.patch.dict(sys.modules, {"render": fake}):
                self.assertTrue(jr.run_one(BASE, "s", "", rec))
        posts = [c for c in rec.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertTrue(all("/file?name=" in c[1] and c[3] == "image/png" for c in posts[:2]))
        self.assertIn("poster%3A2026-10-21%3A1", posts[0][1])
        self.assertEqual(posts[-1][1], BASE + "/poster%3A2026-10-21%3A1/complete")
        self.assertTrue(posts[-1][2]["ok"])

    def test_failure_is_reported_with_the_real_reason(self):
        job = {"id": "poster:2026-10-21:1", "type": "poster", "payload": {}}
        rec = Recorder(job)
        fake = mock.Mock()
        fake.render.side_effect = ValueError("海报自检未通过：英文desktop版出现中文字符")
        with mock.patch.dict(sys.modules, {"render": fake}):
            self.assertTrue(jr.run_one(BASE, "s", "", rec))
        last = rec.calls[-1]
        self.assertFalse(last[2]["ok"])
        self.assertIn("自检未通过", last[2]["error"])

    def test_ledger_job_needs_the_notion_token_and_hides_it_in_errors(self):
        job = {"id": "ledger:2026-10-21:1", "type": "ledger", "payload": {"day": "2026-10-21"}}
        rec = Recorder(job)
        jr.run_one(BASE, "s", "", rec)
        self.assertFalse(rec.calls[-1][2]["ok"])
        self.assertIn("NOTION_TOKEN", rec.calls[-1][2]["error"])
        rec = Recorder(job)
        fake = mock.Mock()
        fake.apply.side_effect = RuntimeError("Notion HTTP 403 secret-token-123")
        with mock.patch.dict(sys.modules, {"ledger_write": fake}):
            jr.run_one(BASE, "s", "secret-token-123", rec)
        self.assertNotIn("secret-token-123", rec.calls[-1][2]["error"])
        rec = Recorder(job)
        fake.apply.side_effect = None
        fake.apply.return_value = ["当前在场新增 BTC 第38期"]
        with mock.patch.dict(sys.modules, {"ledger_write": fake}):
            jr.run_one(BASE, "s", "tok", rec)
        self.assertEqual(rec.calls[-1][2]["result"], {"actions": ["当前在场新增 BTC 第38期"]})

    def test_peek_reports_without_leasing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.txt"
            env = {"PIPELINE_SECRET": "s", "WORKER_INGEST_URL": "https://desk.example/pipeline/snapshot", "GITHUB_OUTPUT": str(out)}
            rec = Recorder({"id": "x", "type": "poster"})
            self.assertEqual(jr.main(["--peek"], rec, env), 0)
            self.assertEqual(out.read_text().strip(), "has_job=true")
            self.assertTrue(rec.calls[0][1].endswith("/next?peek=1"))
            self.assertEqual(jr.main(["--peek"], Recorder(None), env), 0)
            self.assertIn("has_job=false", out.read_text())

    def test_missing_secrets_is_an_error(self):
        self.assertEqual(jr.main([], Recorder(None), {}), 1)


if __name__ == "__main__":
    unittest.main()
