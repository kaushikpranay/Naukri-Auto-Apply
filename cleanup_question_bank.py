"""
cleanup_question_bank.py

Finds and removes question_bank rows where answer IS NULL or TRIM(answer) = ''.
These are questions the bot encountered but never answered — they have no value
and pollute coverage metrics.

Run:
    .venv/Scripts/python.exe cleanup_question_bank.py
    .venv/Scripts/python.exe cleanup_question_bank.py --dry-run   # preview only
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "database" / "jobs.db"

_DIVIDER = "-" * 90


def _fetch_null_rows(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, question_key, question_text, field_type, usage_count, created_at
        FROM question_bank
        WHERE answer IS NULL OR TRIM(answer) = ''
        ORDER BY usage_count DESC, id ASC
    """)
    return [dict(r) for r in c.fetchall()]


def _print_table(rows: list[dict]) -> None:
    print(_DIVIDER)
    print(f"  {'ID':>4}  {'USES':>4}  {'TYPE':<14}  {'KEY':<35}  {'QUESTION TEXT'}")
    print(_DIVIDER)
    for r in rows:
        key = r["question_key"][:35]
        text = r["question_text"][:50].replace("\n", " ")
        print(f"  {r['id']:>4}  {r['usage_count']:>4}  {(r['field_type'] or '?'):<14}  {key:<35}  {text}")
    print(_DIVIDER)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Remove null-answer rows from question_bank")
    p.add_argument("--dry-run", action="store_true", help="Preview only — do not delete")
    p.add_argument("--db", default=str(DB_PATH), help="Override DB path")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    rows = _fetch_null_rows(conn)

    if not rows:
        print("Nothing to clean — all question_bank rows already have answers.")
        conn.close()
        return 0

    print(f"\nFound {len(rows)} question_bank row(s) with no answer:\n")
    _print_table(rows)

    if args.dry_run:
        print("\n[dry-run] No changes made.")
        conn.close()
        return 0

    answer = input(f"\nDelete these {len(rows)} rows? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted — nothing deleted.")
        conn.close()
        return 0

    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM question_bank WHERE id IN ({placeholders})", ids)
    conn.commit()

    # Confirm
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM question_bank")
    remaining = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM question_bank WHERE answer IS NULL OR TRIM(answer) = ''")
    still_null = c.fetchone()[0]

    conn.close()

    print(f"\nDeleted {len(ids)} row(s).")
    print(f"question_bank now holds {remaining} rows ({still_null} with null/empty answers).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
