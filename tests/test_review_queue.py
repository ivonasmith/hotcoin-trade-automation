import tempfile
import unittest
from pathlib import Path
from review_queue import Queue, timestamp, publication_time


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = Queue(str(Path(self.tmp.name) / "queue.db"), "123")
        self.payload = {"text": "待审核测试，不发送", "snapshot_id": "test-only",
                        "images": [{"path": "poster.png", "sha256": "a" * 64}]}
        self.row = self.q.stage("2026-10-19", "zh", self.payload,
                                "2026-10-19T20:10:00+08:00")
        self.id = self.row["id"]
        self.now = timestamp("2026-10-19T20:00:00+08:00")

    def tearDown(self):
        self.q.close()
        self.tmp.cleanup()

    def approve(self):
        self.q.decide(self.id, 1, "approve_text", "123")
        self.q.decide(self.id, 1, "approve_images", "123")

    def check(self):
        self.q.preflight(self.id, 1, self.row["fingerprint"], self.now, True,
                         "test-source-and-length-ok")

    def test_timezone(self):
        self.assertEqual(timestamp(publication_time("2026-10-19", "zh")),
                         timestamp("2026-10-19T12:00:00+00:00"))
        self.assertEqual(timestamp(publication_time("2026-10-19", "en")),
                         self.now + 3600)

    def test_owner_and_order(self):
        with self.assertRaises(PermissionError):
            self.q.decide(self.id, 1, "approve_text", "999")
        with self.assertRaises(ValueError):
            self.q.decide(self.id, 1, "approve_images", "123")
        self.assertIsNone(self.q.claim(self.id, self.now))

    def test_changes_revoke_both_approvals_and_old_button(self):
        self.approve()
        self.payload["text"] = "修改后必须重新审核"
        row = self.q.stage("2026-10-19", "zh", self.payload,
                           "2026-10-19T20:10:00+08:00")
        self.assertEqual(row["state"], "text_review")
        with self.assertRaises(ValueError):
            self.q.decide(self.id, 1, "approve_text", "123")
        with self.assertRaises(ValueError):
            self.check()

    def test_preflight_required_and_expires(self):
        self.approve()
        self.assertIsNone(self.q.claim(self.id, self.now))
        self.check()
        self.assertIsNone(self.q.claim(self.id, self.now - 1))
        self.assertIsNone(self.q.claim(self.id, self.now + 300))

    def test_atomic_claim_and_uncertain_never_retry(self):
        self.approve()
        self.check()
        other = Queue(str(Path(self.tmp.name) / "queue.db"), "123")
        self.assertIsNotNone(self.q.claim(self.id, self.now))
        self.assertIsNone(other.claim(self.id, self.now))
        self.q.finish(self.id)
        self.assertIsNone(other.claim(self.id, self.now + 1))
        self.assertEqual(other.get(self.id)["state"], "uncertain")
        other.close()

    def test_crash_recovery_and_published_no_overwrite(self):
        self.approve()
        self.check()
        self.q.claim(self.id, self.now)
        self.q.finish(self.id, "test-post-id")
        with self.assertRaises(ValueError):
            self.q.stage("2026-10-19", "zh", self.payload,
                         "2026-10-19T20:10:00+08:00")
        self.q.recover()
        self.assertEqual(self.q.get(self.id)["state"], "published")

    def test_recover_inflight(self):
        self.approve()
        self.check()
        self.q.claim(self.id, self.now)
        self.q.recover()
        self.assertEqual(self.q.get(self.id)["state"], "uncertain")

    def test_event_failure_holds(self):
        self.approve()
        self.q.preflight(self.id, 1, self.row["fingerprint"], self.now, False,
                         "重大事件结果与稿件冲突")
        self.assertEqual(self.q.get(self.id)["state"], "held")
        self.assertIsNone(self.q.claim(self.id, self.now))

    def test_missed_deadline_does_not_publish_later(self):
        self.approve()
        self.check()
        self.assertIsNone(self.q.claim(self.id, self.now + 601))
        self.assertEqual(self.q.get(self.id)["state"], "expired")


if __name__ == "__main__":
    unittest.main()
