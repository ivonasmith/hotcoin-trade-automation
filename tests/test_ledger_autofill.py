import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PIPE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPE))
import fetch_ledger  # noqa: E402
import ledger_autofill as auto  # noqa: E402
import ledger_sync  # noqa: E402
import ledger_write  # noqa: E402


ACTIVE = [{"issue": "36", "symbol": "SOL", "day": "2026-10-19"}, {"issue": "37", "symbol": "XRP", "day": "2026-10-21"}]


def runtime_with(proposals, ok=True):
    tmp = tempfile.mkdtemp()
    (Path(tmp) / "ledger.json").write_text(json.dumps({"ok": ok, "active": ACTIVE, "proposals": proposals}))
    return tmp


class AutofillTest(unittest.TestCase):
    def test_writes_only_fills_then_refreshes_ledger_and_check(self):
        rt = runtime_with([{"type": "fill", "issue": "36", "symbol": "SOL", "date": "2026-10-21"}, {"type": "expire", "issue": "1"}])
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t"}), \
                mock.patch.object(ledger_write, "apply_fills", return_value=["第36期SOL成交"]) as apply, \
                mock.patch.object(fetch_ledger, "main") as fetch, mock.patch.object(ledger_sync, "main") as sync:
            self.assertEqual(auto.main(rt), 0)
        self.assertEqual([p["type"] for p in apply.call_args.args[0]], ["fill"])
        fetch.assert_called_once_with(rt)
        sync.assert_called_once_with(rt)

    def test_stop_moves_are_written_with_the_fills(self):
        rt = runtime_with([{"type": "stop_move", "issue": "36", "symbol": "SOL", "date": "2026-10-25", "price": 105.61},
                           {"type": "stop_out", "issue": "37", "symbol": "XRP", "price": 1.5}])
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t"}), \
                mock.patch.object(ledger_write, "apply_fills", return_value=["第36期SOL止损上移"]) as apply, \
                mock.patch.object(fetch_ledger, "main"), mock.patch.object(ledger_sync, "main"):
            auto.main(rt)
        self.assertEqual([p["type"] for p in apply.call_args.args[0]], ["stop_move"])

    def test_touch_on_the_publish_day_is_left_for_a_human(self):
        rt = runtime_with([{"type": "fill", "issue": "37", "symbol": "XRP", "date": "2026-10-21"},
                           {"type": "fill", "issue": "36", "symbol": "SOL", "date": "2026-10-20"}])
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t"}), \
                mock.patch.object(ledger_write, "apply_fills", return_value=["x"]) as apply, \
                mock.patch.object(fetch_ledger, "main"), mock.patch.object(ledger_sync, "main"):
            auto.main(rt)
        self.assertEqual([(p["symbol"], p["date"]) for p in apply.call_args.args[0]], [("SOL", "2026-10-20")])
        only_same_day = runtime_with([{"type": "fill", "issue": "37", "symbol": "XRP", "date": "2026-10-21"}])
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t"}), mock.patch.object(ledger_write, "apply_fills") as apply:
            auto.main(only_same_day)
        apply.assert_not_called()

    def test_nothing_to_do_touches_nothing(self):
        for rt in (runtime_with([]), runtime_with([{"type": "expire"}]), runtime_with([{"type": "fill"}], ok=False)):
            with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t"}), \
                    mock.patch.object(ledger_write, "apply_fills") as apply:
                self.assertEqual(auto.main(rt), 0)
            apply.assert_not_called()

    def test_failure_or_missing_token_never_blocks_the_upload(self):
        rt = runtime_with([{"type": "fill", "issue": "36", "symbol": "SOL"}])
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "", "NOTION_API_KEY": ""}):
            self.assertEqual(auto.main(rt), 0)
        with mock.patch.dict(os.environ, {"NOTION_TOKEN": "t"}), \
                mock.patch.object(ledger_write, "apply_fills", side_effect=RuntimeError("boom")):
            self.assertEqual(auto.main(rt), 0)
        self.assertEqual(auto.main("/nonexistent"), 0)


if __name__ == "__main__":
    unittest.main()
