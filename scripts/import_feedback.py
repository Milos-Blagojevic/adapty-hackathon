"""Import the hand-written hard-case feedback (data/feedback_extra.json) as `new` tickets.

    python scripts/import_feedback.py                # into config.DB_PATH
    python scripts/import_feedback.py --db some.sqlite

Each item becomes a ticket with id = the item id (`x-001`, ...), through db.insert_feedback. Idempotent:
ids already in the DB are skipped. Seeds the 60 tickets first when the DB is empty (db.seed only loads
an empty table, so importing into an empty DB first would otherwise block the seed). No model calls,
no Adapty calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config, db  # noqa: E402
from resolve.models import FeedbackRequest  # noqa: E402

FEEDBACK_PATH = config.ROOT / "data" / "feedback_extra.json"


def load(path: Path = FEEDBACK_PATH) -> list[dict]:
    return json.loads(Path(path).read_text())["items"]


def import_items(conn: db.Database, items: list[dict]) -> tuple[int, int]:
    """Insert the items that are not in the DB yet. Returns (inserted, skipped)."""
    inserted = skipped = 0
    for item in items:
        request = FeedbackRequest(**{k: v for k, v in item.items() if k in FeedbackRequest.model_fields})
        if db.get_ticket(conn, item["id"]) is not None:
            skipped += 1
            continue
        db.insert_feedback(
            conn,
            request.text,
            source=request.source,
            customer_user_id=request.customer_user_id,
            email=request.email,
            subject=request.subject,
            ticket_id=item["id"],
        )
        inserted += 1
    return inserted, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--file", type=Path, default=FEEDBACK_PATH)
    args = parser.parse_args()

    conn = db.connect(args.db)
    seeded = db.seed(conn, config.ROOT / "data" / "tickets.json")
    inserted, skipped = import_items(conn, load(args.file))
    by_source = {r["source"]: r["n"] for r in conn.execute("SELECT source, COUNT(*) AS n FROM tickets GROUP BY source")}
    total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    print(f"{args.db}: seeded {seeded}, inserted {inserted}, skipped {skipped} (already there); {total} tickets, by source {by_source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
