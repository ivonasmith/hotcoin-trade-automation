"""Version-bound, persistent two-stage approval and publication queue.

No network calls here. Telegram and X adapters must call this core; credentials
and provider requests are deliberately kept outside the database.
"""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

BJT = timezone(timedelta(hours=8))


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return result.timestamp()


def publication_time(day, language):
    if language not in ("zh", "en"):
        raise ValueError("仅支持 zh/en")
    hour = 20 if language == "zh" else 21
    return datetime.fromisoformat(day).replace(
        hour=hour, minute=0, second=0, microsecond=0, tzinfo=BJT
    ).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


class Queue:
    def __init__(self, path, owner_id):
        self.owner_id = str(owner_id)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS editions (
          id TEXT PRIMARY KEY, day TEXT NOT NULL, language TEXT NOT NULL,
          revision INTEGER NOT NULL, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
          state TEXT NOT NULL, due REAL NOT NULL, expires REAL NOT NULL,
          verified_until REAL, post_id TEXT,
          UNIQUE(day, language)
        );
        CREATE TABLE IF NOT EXISTS audit (
          at TEXT NOT NULL, edition TEXT NOT NULL, revision INTEGER,
          action TEXT NOT NULL, actor TEXT NOT NULL
        );
        """)

    def close(self):
        self.db.close()

    def get(self, edition):
        row = self.db.execute("SELECT * FROM editions WHERE id=?", (edition,)).fetchone()
        if row is None:
            raise ValueError("稿件不存在")
        return dict(row)

    def record(self, edition, revision, action, actor):
        self.db.execute("INSERT INTO audit VALUES (?,?,?,?,?)", (
            datetime.now(timezone.utc).isoformat(), edition, revision, action, str(actor)
        ))

    def stage(self, day, language, payload, expires_at):
        """Replacing text/images/sources always revokes BOTH approvals."""
        due = timestamp(publication_time(day, language))
        expires = timestamp(expires_at)
        if expires <= due:
            raise ValueError("有效期必须晚于发布时间")
        if not isinstance(payload.get("text"), str) or not payload["text"].strip():
            raise ValueError("缺少正文")
        images = payload.get("images", [])
        if not 1 <= len(images) <= 4:
            raise ValueError("每份稿件需要1—4张图片")
        for image in images:
            if set(image) != {"path", "sha256"} or len(image["sha256"]) != 64:
                raise ValueError("图片必须包含路径和SHA256")
        # Preflight must verify factual freshness and actual account text limits.
        if not payload.get("snapshot_id"):
            raise ValueError("必须标识输入数据快照")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM editions WHERE day=? AND language=?",
                                  (day, language)).fetchone()
            if old and old["state"] in ("sending", "uncertain", "published"):
                raise ValueError("已发出或结果待核实的稿件不能覆盖")
            edition = old["id"] if old else uuid.uuid4().hex[:12]
            revision = old["revision"] + 1 if old else 1
            self.db.execute("""INSERT INTO editions VALUES (?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(day,language) DO UPDATE SET revision=excluded.revision,
              payload=excluded.payload,fingerprint=excluded.fingerprint,
              state='text_review',due=excluded.due,expires=excluded.expires,
              verified_until=NULL,post_id=NULL""", (
                edition, day, language, revision, json.dumps(payload, ensure_ascii=False),
                digest(payload), "text_review", due, expires, None, None))
            self.record(edition, revision, "staged", "pipeline")
            self.db.execute("COMMIT")
            return self.get(edition)
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def decide(self, edition, revision, action, actor):
        if str(actor) != self.owner_id:
            raise PermissionError("只有指定审核人可以操作")
        transitions = {
            "approve_text": ("text_review", "image_review"),
            "approve_images": ("image_review", "queued"),
        }
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(edition)
            if row["revision"] != revision:
                raise ValueError("旧版本按钮已失效")
            if action == "hold":
                if row["state"] in ("sending", "uncertain", "published"):
                    raise ValueError("发送已开始，请核实发布结果")
                state = "held"
            else:
                before, state = transitions[action]
                if row["state"] != before:
                    raise ValueError("审核顺序不符或按钮已经处理")
            self.db.execute("UPDATE editions SET state=?,verified_until=NULL WHERE id=?",
                            (state, edition))
            self.record(edition, revision, action, actor)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def preflight(self, edition, revision, fingerprint, now, passed, reason):
        """Adapter must check sources/events, image hashes and length BEFORE this.

        A pass lasts five minutes. Failure holds the edition for a new revision.
        No model/network call occurs while the database lock is held.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(edition)
            if (row["revision"], row["fingerprint"], row["state"]) != (
                revision, fingerprint, "queued"
            ):
                raise ValueError("复核期间稿件或审核状态发生变化")
            self.db.execute("UPDATE editions SET state=?,verified_until=? WHERE id=?",
                            ("queued" if passed else "held", now + 300 if passed else None,
                             edition))
            self.record(edition, revision, "preflight:" + reason, "preflight")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def claim(self, edition, now):
        """Atomic claim: only one process may begin a publication attempt."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(edition)
            if row["state"] != "queued" or now < row["due"]:
                result = None
            elif now >= row["expires"]:
                self.db.execute("UPDATE editions SET state='expired' WHERE id=?", (edition,))
                self.record(edition, row["revision"], "expired", "scheduler")
                result = None
            elif row["verified_until"] is None or now >= row["verified_until"]:
                result = None
            else:
                self.db.execute("UPDATE editions SET state='sending' WHERE id=?", (edition,))
                self.record(edition, row["revision"], "sending", "scheduler")
                result = row
            self.db.execute("COMMIT")
            return result
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, edition, post_id=None):
        """A timeout is uncertain, NEVER an automatic retry of POST /tweets."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(edition)
            if row["state"] != "sending":
                raise ValueError("没有进行中的发送")
            state = "published" if post_id else "uncertain"
            self.db.execute("UPDATE editions SET state=?,post_id=? WHERE id=?",
                            (state, post_id, edition))
            self.record(edition, row["revision"], state, "publisher")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def recover(self):
        """Call once at daemon startup, before admitting a new publisher."""
        self.db.execute("UPDATE editions SET state='uncertain' WHERE state='sending'")
