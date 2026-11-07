"""SQLite: tickets with their cached profile, proposal and action; Level 1 insights; A/B drafts.

One file (`config.DB_PATH`, or the committed `data/demo.sqlite` in DEMO_MODE). The schema is created
on first use and `data/tickets.json` is loaded into it when the tickets table is empty.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    customer_user_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    demo INTEGER NOT NULL DEFAULT 0,
    rule TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'new',
    category TEXT,
    profile_json TEXT,
    profile_fetched_at TEXT,
    profile_error TEXT,
    proposal_json TEXT,
    proposal_at TEXT,
    proposal_error TEXT,
    model_calls INTEGER NOT NULL DEFAULT 0,
    action_json TEXT,
    action_at TEXT,
    action_error TEXT,
    reply_sent_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    insights_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ab_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    placement_id TEXT NOT NULL,
    test_id TEXT NOT NULL,
    title TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

TICKET_COLUMNS = (
    "id", "customer_user_id", "subject", "body", "created_at", "demo", "rule", "context_json", "status", "category",
    "profile_json", "profile_fetched_at", "profile_error", "proposal_json", "proposal_at", "proposal_error",
    "model_calls", "action_json", "action_at", "action_error", "reply_sent_at", "updated_at",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    """One SQLite file, one connection per thread.

    FastAPI runs sync routes in a threadpool; a single `sqlite3` connection shared across threads
    raised `InterfaceError` and returned phantom rows under concurrent requests. Each thread gets
    its own connection lazily; the schema is created once. Supports the `with conn:` transaction
    protocol and `execute`/`executemany`, which is all this module uses.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def execute(self, sql: str, parameters=()) -> sqlite3.Cursor:
        return self._connection().execute(sql, parameters)

    def executemany(self, sql: str, parameters) -> sqlite3.Cursor:
        return self._connection().executemany(sql, parameters)

    def __enter__(self) -> sqlite3.Connection:
        return self._connection().__enter__()

    def __exit__(self, *exc) -> None:
        self._connection().__exit__(*exc)

    def close(self) -> None:
        """Close the calling thread's connection only; other threads' connections close when they end."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def connect(path: Path) -> Database:
    return Database(path)


def seed(conn: Database, tickets_json: Path) -> int:
    """Load the seed file when the table is empty. Returns the number of rows inserted."""
    if conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]:
        return 0
    rows = json.loads(tickets_json.read_text())
    with conn:
        conn.executemany(
            "INSERT INTO tickets (id, customer_user_id, subject, body, created_at, demo, rule, context_json, status, updated_at) "
            "VALUES (:id, :customer_user_id, :subject, :body, :created_at, :demo, :rule, :context_json, 'new', :updated_at)",
            [dict(row, demo=int(row["demo"]), context_json=json.dumps(row.get("context", {})), updated_at=now_iso()) for row in rows],
        )
    return len(rows)


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    ticket = dict(row)
    ticket["demo"] = bool(ticket["demo"])
    ticket["context"] = json.loads(ticket["context_json"] or "{}")
    ticket["profile"] = json.loads(ticket["profile_json"]) if ticket["profile_json"] else None
    ticket["proposal"] = json.loads(ticket["proposal_json"]) if ticket["proposal_json"] else None
    ticket["action"] = json.loads(ticket["action_json"]) if ticket["action_json"] else None
    return ticket


def get_ticket(conn: Database, ticket_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())


def list_tickets(conn: Database, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute("SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        rows = conn.execute("SELECT * FROM tickets ORDER BY created_at DESC")
    return [_row(r) for r in rows]


def status_counts(conn: Database) -> dict[str, int]:
    return {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM tickets GROUP BY status")}


def update_ticket(conn: Database, ticket_id: str, only_status: tuple[str, ...] | None = None, **fields) -> bool:
    """Write `fields`; with `only_status`, only while the ticket is still in one of those statuses.

    Returns whether a row changed. The conditional form is what keeps a slow Level 2 run from
    overwriting a ticket the owner approved or dismissed in the meantime.
    """
    unknown = set(fields) - set(TICKET_COLUMNS)
    if unknown:
        raise ValueError(f"unknown ticket columns: {sorted(unknown)}")
    for key in ("profile", "proposal", "action", "context"):
        if key in fields:
            raise ValueError(f"pass {key}_json, not {key}")
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    where = "id = :id"
    params = dict(fields, id=ticket_id)
    if only_status is not None:
        if not only_status:
            raise ValueError("only_status must name at least one status")
        placeholders = ", ".join(f":s{i}" for i in range(len(only_status)))
        where += f" AND status IN ({placeholders})"
        params.update({f"s{i}": status for i, status in enumerate(only_status)})
    with conn:
        cursor = conn.execute(f"UPDATE tickets SET {assignments} WHERE {where}", params)
    return cursor.rowcount == 1


def set_status_if(conn: Database, ticket_id: str, expected: str, new: str) -> bool:
    """Atomic status transition; False when the ticket is not in `expected` (the approve-twice guard)."""
    with conn:
        cursor = conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (new, now_iso(), ticket_id, expected),
        )
    return cursor.rowcount == 1


def reserve_model_call(conn: Database, ticket_id: str, cap: int) -> bool:
    """Take one model call from the ticket's budget atomically; False when the cap is reached.

    Counted before the call, so two concurrent runs cannot both spend the last call.
    """
    with conn:
        cursor = conn.execute(
            "UPDATE tickets SET model_calls = model_calls + 1, updated_at = ? WHERE id = ? AND model_calls < ?",
            (now_iso(), ticket_id, cap),
        )
    return cursor.rowcount == 1


def save_insights(conn: Database, insights: dict) -> None:
    with conn:
        conn.execute(
            "INSERT INTO insights (id, insights_json, created_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET insights_json = excluded.insights_json, created_at = excluded.created_at",
            (json.dumps(insights), now_iso()),
        )


def get_insights(conn: Database) -> dict | None:
    row = conn.execute("SELECT insights_json, created_at FROM insights WHERE id = 1").fetchone()
    if row is None:
        return None
    return dict(json.loads(row["insights_json"]), created_at=row["created_at"])


def save_ab_draft(conn: Database, placement_id: str, test_id: str, title: str, draft: dict) -> None:
    with conn:
        conn.execute(
            "INSERT INTO ab_drafts (placement_id, test_id, title, draft_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (placement_id, test_id, title, json.dumps(draft), now_iso()),
        )


def list_ab_drafts(conn: Database) -> list[dict]:
    rows = conn.execute("SELECT * FROM ab_drafts ORDER BY id DESC")
    return [dict(r, draft=json.loads(r["draft_json"])) for r in rows]
