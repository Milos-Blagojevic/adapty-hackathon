"""Build data/demo.sqlite, the committed snapshot DEMO_MODE serves without keys.

    python scripts/snapshot.py [--db data/resolve.sqlite] [--out data/demo.sqlite]

Copies the live DB with SQLite's backup API (WAL included), then puts the demo tickets back to
`proposed` so the happy path (open → approve) can be clicked in demo mode. Cached profiles and
proposals stay as they are; the demo-mode Approve changes local state only.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config, db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--out", type=Path, default=config.DEMO_DB_PATH)
    args = parser.parse_args()
    if not args.db.exists():
        print(f"no DB at {args.db}; run the app first")
        return 1

    for suffix in ("", "-wal", "-shm"):
        Path(str(args.out) + suffix).unlink(missing_ok=True)
    source = sqlite3.connect(args.db)
    target = sqlite3.connect(args.out)
    source.backup(target)
    source.close()
    target.execute("PRAGMA journal_mode=DELETE")
    with target:
        target.execute(
            "UPDATE tickets SET status = 'proposed', action_json = NULL, action_at = NULL, action_error = NULL, reply_sent_at = NULL, updated_at = ? "
            "WHERE demo = 1 AND proposal_json IS NOT NULL",
            (db.now_iso(),),
        )
        target.execute("UPDATE tickets SET status = 'triaged', updated_at = ? WHERE demo = 1 AND proposal_json IS NULL AND profile_json IS NOT NULL", (db.now_iso(),))
    counts = {r[0]: r[1] for r in target.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status")}
    proposals = target.execute("SELECT COUNT(*) FROM tickets WHERE proposal_json IS NOT NULL").fetchone()[0]
    profiles = target.execute("SELECT COUNT(*) FROM tickets WHERE profile_json IS NOT NULL").fetchone()[0]
    insights = target.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
    target.close()
    print(f"{args.out}: statuses {counts}, {profiles} profiles, {proposals} proposals, insights cached: {bool(insights)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
