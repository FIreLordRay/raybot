"""One-time migration: single-user progress/meta -> per-user progress/user_state.

Safe to re-run — checks whether progress already has a user_name column
(i.e. this migration already ran) and does nothing if so.

    python migrate_to_multiuser.py [target_user_name]

target_user_name defaults to the current Windows login name (matches what
practice.py uses by default) — that's who the existing solved/streak data
gets attributed to.
"""

import getpass
import sys

import db

DEFAULT_USER = getpass.getuser()


def already_migrated(conn):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(progress)").fetchall()]
    return "user_name" in cols


def main():
    target_user = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USER
    conn = db.get_conn()

    if already_migrated(conn):
        print("Already migrated (progress table has a user_name column) — nothing to do.")
        conn.close()
        return

    print(f"Migrating existing single-user progress to user_name={target_user!r}...")

    conn.execute("ALTER TABLE progress RENAME TO progress_old")
    conn.execute("""
        CREATE TABLE progress (
            problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
            user_name TEXT NOT NULL,
            solved INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            solved_date TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (problem_id, user_name)
        )
    """)
    old_rows = conn.execute("SELECT * FROM progress_old").fetchall()
    for r in old_rows:
        conn.execute(
            """INSERT INTO progress (problem_id, user_name, solved, skipped, solved_date, attempts)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (r["problem_id"], target_user, r["solved"], r["skipped"], r["solved_date"], r["attempts"]),
        )
    conn.execute("DROP TABLE progress_old")
    print(f"  migrated {len(old_rows)} progress rows")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_state (
            user_name TEXT PRIMARY KEY,
            streak INTEGER NOT NULL DEFAULT 0,
            last_solved_date TEXT,
            current_id TEXT
        )
    """)
    meta_rows = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM meta").fetchall()}
    streak = int(meta_rows.get("streak", 0) or 0)
    last_solved_date = meta_rows.get("last_solved_date")
    current_id = meta_rows.get("current_id") or None
    conn.execute(
        "INSERT OR REPLACE INTO user_state (user_name, streak, last_solved_date, current_id) VALUES (?, ?, ?, ?)",
        (target_user, streak, last_solved_date, current_id),
    )
    print(f"  migrated user_state: streak={streak}, last_solved_date={last_solved_date}, current_id={current_id}")

    conn.execute("DELETE FROM meta WHERE key IN ('streak', 'last_solved_date', 'current_id')")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
