"""Daily collection step: record fills in the Notion ledger as soon as the price has traded through the entry.

Runs after fetch_ledger.py and ledger_sync.py. It writes only 'fill' proposals (see ledger_write.apply_fills), then
re-reads the ledger and re-runs the check so the uploaded snapshot already shows the position as held. Never blocks
the snapshot upload: any failure is a warning and the snapshot goes up with the fill still listed as a proposal
(the poster draws those as held too).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
BEIJING = timezone(timedelta(hours=8))


def main(runtime="runtime"):
    path = Path(runtime) / "ledger.json"
    try:
        ledger = json.loads(path.read_text())
        fills = [p for p in (ledger.get("proposals") or []) if p.get("type") == "fill"] if ledger.get("ok") else []
        # A touch on the publish day itself may have happened BEFORE the order was published (daily bars cannot tell),
        # and the ledger has precedents both ways, so those stay for a human; only later days are unambiguous.
        published = {(str(r.get("issue")), r.get("symbol")): r.get("day") for r in ledger.get("active") or []}
        held_back = [p for p in fills if str(p.get("date") or "") <= str(published.get((str(p.get("issue")), p.get("symbol"))) or "")]
        for item in held_back:
            print("::notice title=发布当天触及，未自动记成交::" + str(item.get("text", ""))[:200].replace("%", "%25"), flush=True)
        fills = [p for p in fills if p not in held_back]
        if not fills:
            return 0
        token = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY") or ""
        if not token:
            raise RuntimeError("没有 NOTION_TOKEN，成交不能自动写入台账")
        import fetch_ledger
        import ledger_sync
        import ledger_write
        done = ledger_write.apply_fills(fills, datetime.now(BEIJING).date().isoformat(), token=token,
                                        page_url=os.environ.get("TRADE_LOG_URL") or None)
        for item in done:
            print("::notice title=台账已自动记成交::" + item, flush=True)
        if done:
            fetch_ledger.main(runtime)      # fresh status, counts and closed list
            ledger_sync.main(runtime)       # proposals recomputed against the new status
    except Exception as exc:  # noqa: BLE001 - must never block the snapshot upload
        print("::warning title=成交自动写入台账未完成::" + str(exc)[:300].replace("%", "%25").replace("\n", " "), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "runtime"))
