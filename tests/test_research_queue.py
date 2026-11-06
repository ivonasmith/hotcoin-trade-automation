import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))
import hotcoin_host as host  # noqa: E402


class Rig:
    def __init__(self, tmp, queue, script_ok=True, fail_times=0):
        self.tmp = Path(tmp)
        (self.tmp / "q").mkdir()
        (self.tmp / "q" / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
        if script_ok:
            (self.tmp / "q" / "rep.py").write_text("# 占位", encoding="utf-8")
        self.docs, self.msgs, self.runs, self.fail_times = [], [], [], fail_times

    def runner(self, args, env, cwd, timeout=0):
        self.runs.append(args)
        if len(self.runs) <= self.fail_times:
            raise RuntimeError("币安连不上")
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "a.html").write_text("<p>报告</p>", encoding="utf-8")
        (out / "b.json").write_text("{}", encoding="utf-8")
        return 0, "完成：3 个币"

    def go(self):
        return host.run_research({}, self.tmp / "state.json", self.tmp / "q" / "queue.json", self.runner,
                                 lambda env, name, data, caption="": self.docs.append((name, data, caption)),
                                 lambda env, text: self.msgs.append(text), self.tmp / "work")


JOB = {"id": "r1", "script": "rep.py", "args": ["--top", 5], "title": "测试研究"}


class ResearchQueueTest(unittest.TestCase):
    def test_runs_once_sends_files_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            rig = Rig(tmp, [JOB])
            self.assertEqual(rig.go(), ["r1"])
            self.assertEqual([d[0] for d in rig.docs], ["a.html", "b.json"])
            self.assertEqual(rig.docs[0][2], "测试研究")
            self.assertIn("研究报告已发送：测试研究（2 个文件）", rig.msgs[0])
            self.assertEqual(rig.runs[0][-2:], ["--top", "5"])
            self.assertEqual(rig.go(), [])          # 已完成，不再跑
            self.assertEqual(len(rig.runs), 1)

    def test_failure_retries_once_then_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            rig = Rig(tmp, [JOB], fail_times=5)
            rig.go()
            self.assertIn("第 1 次", rig.msgs[0])
            self.assertIn("自动再试一次", rig.msgs[0])
            rig.go()
            self.assertIn("不再自动重试", rig.msgs[1])
            self.assertEqual(rig.go(), [])
            self.assertEqual(len(rig.runs), 2)
            self.assertEqual(rig.docs, [])

    def test_second_attempt_can_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            rig = Rig(tmp, [JOB], fail_times=1)
            rig.go()
            rig.go()
            self.assertEqual(len(rig.docs), 2)
            self.assertEqual(rig.go(), [])

    def test_bad_script_name_or_missing_file_is_never_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            rig = Rig(tmp, [{"id": "x", "script": "../evil.py"}, {"id": "y", "script": "gone.py"}])
            self.assertEqual(rig.go(), [])
            self.assertEqual(rig.runs, [])

    def test_missing_or_broken_queue_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(host.run_research({}, Path(tmp) / "s.json", Path(tmp) / "none.json"), [])
            (Path(tmp) / "bad.json").write_text("乱", encoding="utf-8")
            self.assertEqual(host.run_research({}, Path(tmp) / "s.json", Path(tmp) / "bad.json"), [])

    def test_shipped_queue_is_valid(self):
        queue = json.loads((ROOT / "research" / "queue.json").read_text(encoding="utf-8"))
        for job in queue:
            self.assertTrue((ROOT / "research" / job["script"]).is_file())
            self.assertTrue(job["id"])


if __name__ == "__main__":
    unittest.main()
