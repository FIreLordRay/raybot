"""SQLite-backed curriculum + per-user progress store, shared by practice.py,
discord_raybot.py, and dashboard.py.

Every person using Raybot (you on the CLI, each Discord user, each dashboard
visitor) has their own solved/skipped/attempts/streak state — the curriculum
itself (problems, categories, tests) is the only thing that's shared.

Schema:
    categories(id, name, sort_order)
    problems(id, category_id, title, difficulty, prompt, func_name, starter,
             hint, lesson, is_class, source, sort_order, created_at)
    tests(id, problem_id, seq, spec)          -- spec is JSON, see below
    progress(problem_id, user_name, solved, skipped, solved_date, attempts)
                                               -- PRIMARY KEY (problem_id, user_name)
    user_state(user_name, streak, last_solved_date, current_id)
                                               -- one row per person
    quiz_history(id, date, score, total, difficulty, category, user_name)
    solve_log(id, problem_id, user_name, mode, date)  -- one row per solve event,
                                               -- for leaderboards/badges/activity heatmap
    submissions(id, problem_id, user_name, code, passed, total, error, created_at)
                                               -- one row per graded attempt (pass or fail),
                                               -- for a real per-problem submission history
    meta(key, value)                          -- global settings, not per-user

Test spec JSON shapes:
    {"type": "args", "args": [...], "expected": <value>}
    {"type": "check", "code": "<python source defining a function check(m)>"}
"""

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "curriculum.db"

DIFFICULTY_BUCKETS = {1: "easy", 2: "medium", 3: "hard", 4: "hard"}


def difficulty_bucket(difficulty):
    return DIFFICULTY_BUCKETS.get(difficulty, "hard")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS problems (
        id TEXT PRIMARY KEY,
        category_id INTEGER NOT NULL REFERENCES categories(id),
        title TEXT NOT NULL,
        difficulty INTEGER NOT NULL,
        prompt TEXT NOT NULL,
        func_name TEXT NOT NULL,
        starter TEXT NOT NULL,
        hint TEXT,
        lesson TEXT,
        is_class INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'curated',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        spec TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS progress (
        problem_id TEXT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
        user_name TEXT NOT NULL,
        solved INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        solved_date TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (problem_id, user_name)
    );
    CREATE TABLE IF NOT EXISTS user_state (
        user_name TEXT PRIMARY KEY,
        streak INTEGER NOT NULL DEFAULT 0,
        last_solved_date TEXT,
        current_id TEXT
    );
    CREATE TABLE IF NOT EXISTS quiz_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        score INTEGER NOT NULL,
        total INTEGER NOT NULL,
        difficulty TEXT,
        category TEXT,
        user_name TEXT
    );
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS solve_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        problem_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        mode TEXT NOT NULL,
        date TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        problem_id TEXT NOT NULL,
        user_name TEXT NOT NULL,
        code TEXT NOT NULL,
        passed INTEGER NOT NULL,
        total INTEGER NOT NULL,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()


# --- categories ---

def get_or_create_category(name, sort_order=0):
    conn = get_conn()
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        conn.close()
        return row["id"]
    cur = conn.execute("INSERT INTO categories (name, sort_order) VALUES (?, ?)", (name, sort_order))
    conn.commit()
    cat_id = cur.lastrowid
    conn.close()
    return cat_id


def get_categories():
    conn = get_conn()
    rows = conn.execute("SELECT id, name, sort_order FROM categories ORDER BY sort_order, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- problems ---

def _row_to_problem(conn, row):
    p = dict(row)
    p["is_class"] = bool(p["is_class"])
    test_rows = conn.execute(
        "SELECT spec FROM tests WHERE problem_id = ? ORDER BY seq", (p["id"],)
    ).fetchall()
    p["tests"] = [json.loads(r["spec"]) for r in test_rows]
    cat_row = conn.execute("SELECT name FROM categories WHERE id = ?", (p["category_id"],)).fetchone()
    p["category"] = cat_row["name"] if cat_row else None
    return p


def all_problems(category=None):
    conn = get_conn()
    if category:
        rows = conn.execute(
            """SELECT p.* FROM problems p JOIN categories c ON p.category_id = c.id
               WHERE c.name = ? ORDER BY p.sort_order, p.id""",
            (category,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM problems ORDER BY sort_order, id").fetchall()
    problems = [_row_to_problem(conn, r) for r in rows]
    conn.close()
    return problems


def problem_by_id(problem_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
    if row is None:
        conn.close()
        return None
    p = _row_to_problem(conn, row)
    conn.close()
    return p


def add_problem(id, category, title, difficulty, prompt, func_name, starter, tests,
                hint=None, lesson=None, is_class=False, source="curated", sort_order=None):
    """tests: list of spec dicts, e.g. {"type": "args", "args": [...], "expected": ...}."""
    conn = get_conn()
    category_id = get_or_create_category(category)
    if sort_order is None:
        row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM problems").fetchone()
        sort_order = row["m"] + 1
    conn.execute(
        """INSERT OR REPLACE INTO problems
           (id, category_id, title, difficulty, prompt, func_name, starter, hint, lesson,
            is_class, source, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (id, category_id, title, difficulty, prompt, func_name, starter, hint, lesson,
         int(is_class), source, sort_order),
    )
    conn.execute("DELETE FROM tests WHERE problem_id = ?", (id,))
    for i, spec in enumerate(tests):
        conn.execute(
            "INSERT INTO tests (problem_id, seq, spec) VALUES (?, ?, ?)",
            (id, i, json.dumps(spec)),
        )
    conn.commit()
    conn.close()


def problems_in_bucket(bucket):
    return [p for p in all_problems() if difficulty_bucket(p["difficulty"]) == bucket]


def today_str():
    return date.today().isoformat()


# --- per-user progress ---

def _ensure_progress_row(conn, problem_id, user_name):
    conn.execute(
        "INSERT OR IGNORE INTO progress (problem_id, user_name) VALUES (?, ?)",
        (problem_id, user_name),
    )


def _ensure_user_row(conn, user_name):
    conn.execute("INSERT OR IGNORE INTO user_state (user_name) VALUES (?)", (user_name,))


def get_solved_ids(user_name):
    conn = get_conn()
    rows = conn.execute(
        "SELECT problem_id FROM progress WHERE solved = 1 AND user_name = ?", (user_name,)
    ).fetchall()
    conn.close()
    return {r["problem_id"] for r in rows}


def get_skipped_ids(user_name):
    conn = get_conn()
    rows = conn.execute(
        "SELECT problem_id FROM progress WHERE skipped = 1 AND user_name = ?", (user_name,)
    ).fetchall()
    conn.close()
    return {r["problem_id"] for r in rows}


def get_streak(user_name):
    conn = get_conn()
    row = conn.execute("SELECT streak FROM user_state WHERE user_name = ?", (user_name,)).fetchone()
    conn.close()
    return row["streak"] if row else 0


def get_last_solved_date(user_name):
    conn = get_conn()
    row = conn.execute("SELECT last_solved_date FROM user_state WHERE user_name = ?", (user_name,)).fetchone()
    conn.close()
    return row["last_solved_date"] if row else None


def get_current_id(user_name):
    conn = get_conn()
    row = conn.execute("SELECT current_id FROM user_state WHERE user_name = ?", (user_name,)).fetchone()
    conn.close()
    return row["current_id"] if row and row["current_id"] else None


def set_current_id(user_name, problem_id):
    conn = get_conn()
    _ensure_user_row(conn, user_name)
    conn.execute("UPDATE user_state SET current_id = ? WHERE user_name = ?", (problem_id or None, user_name))
    conn.commit()
    conn.close()


def get_attempts(problem_id, user_name):
    conn = get_conn()
    row = conn.execute(
        "SELECT attempts FROM progress WHERE problem_id = ? AND user_name = ?", (problem_id, user_name)
    ).fetchone()
    conn.close()
    return row["attempts"] if row else 0


def increment_attempts(problem_id, user_name):
    conn = get_conn()
    _ensure_progress_row(conn, problem_id, user_name)
    conn.execute(
        "UPDATE progress SET attempts = attempts + 1 WHERE problem_id = ? AND user_name = ?",
        (problem_id, user_name),
    )
    conn.commit()
    row = conn.execute(
        "SELECT attempts FROM progress WHERE problem_id = ? AND user_name = ?", (problem_id, user_name)
    ).fetchone()
    conn.close()
    return row["attempts"]


def _update_streak(conn, user_name):
    today = today_str()
    row = conn.execute("SELECT streak, last_solved_date FROM user_state WHERE user_name = ?", (user_name,)).fetchone()
    last = row["last_solved_date"] if row else None
    current_streak = row["streak"] if row else 0
    if last == today:
        return
    if last:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        streak = current_streak + 1 if last == yesterday else 1
    else:
        streak = 1
    conn.execute(
        "UPDATE user_state SET streak = ?, last_solved_date = ? WHERE user_name = ?",
        (streak, today, user_name),
    )


def mark_solved(problem_id, user_name="you", mode="cli"):
    """Marks a problem solved for this specific user (idempotent) and always
    logs a solve_log event — even on a re-solve — so leaderboards and the
    activity heatmap reflect who's actually been doing the work. Streak
    counts any correctly-answered question today, new or review."""
    conn = get_conn()
    _ensure_user_row(conn, user_name)
    _ensure_progress_row(conn, problem_id, user_name)
    conn.execute(
        "UPDATE progress SET solved = 1, solved_date = ? WHERE problem_id = ? AND user_name = ?",
        (today_str(), problem_id, user_name),
    )
    conn.execute(
        "INSERT INTO solve_log (problem_id, user_name, mode, date) VALUES (?, ?, ?, ?)",
        (problem_id, user_name, mode, today_str()),
    )
    _update_streak(conn, user_name)
    conn.commit()
    conn.close()
    if get_current_id(user_name) == problem_id:
        set_current_id(user_name, None)


def mark_skipped(problem_id, user_name):
    conn = get_conn()
    _ensure_progress_row(conn, problem_id, user_name)
    conn.execute(
        "UPDATE progress SET skipped = 1 WHERE problem_id = ? AND user_name = ?", (problem_id, user_name)
    )
    conn.commit()
    conn.close()
    if get_current_id(user_name) == problem_id:
        set_current_id(user_name, None)


def next_unsolved(user_name, category=None):
    done = get_solved_ids(user_name) | get_skipped_ids(user_name)
    for p in all_problems(category=category):
        if p["id"] not in done:
            return p["id"]
    return None


def ensure_current(user_name, category=None):
    current = get_current_id(user_name)
    if current and current not in get_solved_ids(user_name) and current not in get_skipped_ids(user_name):
        return current
    new_id = next_unsolved(user_name, category=category)
    set_current_id(user_name, new_id)
    return new_id


# --- race mode (Discord !quiz): a shared/communal pool, not tied to any one
# person's progress. A problem counts as "used" once anyone has won a race
# with it, regardless of whose personal progress reflects a solve. ---

def raced_problem_ids():
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT problem_id FROM solve_log WHERE mode = 'race'").fetchall()
    conn.close()
    return {r["problem_id"] for r in rows}


def next_unraced(category=None):
    used = raced_problem_ids()
    for p in all_problems(category=category):
        if p["id"] not in used:
            return p["id"]
    return None


# --- difficulty / category stats (per-user) ---

def difficulty_bucket_stats(user_name):
    solved = get_solved_ids(user_name)
    buckets = []
    for name in ("easy", "medium", "hard"):
        probs = problems_in_bucket(name)
        total = len(probs)
        done = sum(1 for p in probs if p["id"] in solved)
        pct = round((done / total) * 100, 1) if total else 0
        buckets.append({"name": name, "solved": done, "total": total, "pct": pct})
    return buckets


def category_stats(user_name):
    solved = get_solved_ids(user_name)
    stats = []
    for cat in get_categories():
        probs = all_problems(category=cat["name"])
        total = len(probs)
        done = sum(1 for p in probs if p["id"] in solved)
        pct = round((done / total) * 100, 1) if total else 0
        stats.append({"name": cat["name"], "solved": done, "total": total, "pct": pct})
    return stats


# --- quiz history ---

def record_quiz_attempt(score, total, difficulty=None, category=None, user_name="you"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO quiz_history (date, score, total, difficulty, category, user_name) VALUES (?, ?, ?, ?, ?, ?)",
        (today_str(), score, total, difficulty, category, user_name),
    )
    conn.commit()
    conn.close()


def get_quiz_history(user_name=None, limit=None):
    conn = get_conn()
    if user_name:
        rows = conn.execute("SELECT * FROM quiz_history WHERE user_name = ? ORDER BY id", (user_name,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM quiz_history ORDER BY id").fetchall()
    conn.close()
    history = [dict(r) for r in rows]
    if limit:
        return history[-limit:]
    return history


def quiz_history_by_bucket(bucket_value, key="difficulty", user_name=None):
    return [h for h in get_quiz_history(user_name=user_name) if h.get(key) == bucket_value]


# --- solve log: leaderboard + activity heatmap ---

def get_solve_log(user_name=None, limit=None):
    conn = get_conn()
    if user_name:
        rows = conn.execute("SELECT * FROM solve_log WHERE user_name = ? ORDER BY id", (user_name,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM solve_log ORDER BY id").fetchall()
    conn.close()
    log = [dict(r) for r in rows]
    if limit:
        return log[-limit:]
    return log


def leaderboard(limit=10):
    """Ranks users by distinct problems solved (a re-solve of the same problem
    only counts once per user), then by total solve events as a tiebreaker.
    Global across everyone — the whole point of a leaderboard."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT user_name,
                  COUNT(DISTINCT problem_id) AS distinct_solved,
                  COUNT(*) AS total_events
           FROM solve_log
           GROUP BY user_name
           ORDER BY distinct_solved DESC, total_events DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def user_solve_count(user_name):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(DISTINCT problem_id) AS c FROM solve_log WHERE user_name = ?", (user_name,)
    ).fetchone()
    conn.close()
    return row["c"] if row else 0


def activity_by_day(user_name, days=84):
    """Solve counts per day for the last `days` days (today inclusive), oldest
    first, for this specific user — enough for a ~12-week GitHub-style
    heatmap. Days with zero solves still appear, with count 0."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, COUNT(*) AS c FROM solve_log WHERE user_name = ? GROUP BY date", (user_name,)
    ).fetchall()
    conn.close()
    counts = {r["date"]: r["c"] for r in rows}
    today = date.today()
    result = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        result.append({"date": d, "count": counts.get(d, 0)})
    return result


# --- adaptive difficulty (per-user) ---

def bucket_accuracy(user_name):
    """Recent accuracy per difficulty bucket, from this user's quiz_history —
    used to bias a 'mixed' quiz toward whichever bucket they've been weakest
    in lately. Returns {"easy": 0.0-1.0, ...}; a bucket with no history yet
    defaults to 0.5 (neutral — neither favored nor avoided)."""
    history = get_quiz_history(user_name=user_name)
    acc = {}
    for bucket in ("easy", "medium", "hard"):
        entries = [h for h in history if h.get("difficulty") == bucket and h.get("total")]
        if not entries:
            acc[bucket] = 0.5
            continue
        recent = entries[-5:]
        rate = sum(h["score"] / h["total"] for h in recent) / len(recent)
        acc[bucket] = rate
    return acc


def adaptive_bucket_order(user_name):
    """Difficulty buckets ordered weakest-accuracy-first for this user, so an
    adaptive 'mixed' quiz can prioritize pulling questions from where they
    need the most practice."""
    acc = bucket_accuracy(user_name)
    return sorted(acc, key=lambda b: acc[b])


def adaptive_problem_order(user_name, problems):
    """Reorders a list of problem dicts so ones from this user's weakest
    difficulty bucket (by recent quiz accuracy) come first, without otherwise
    disturbing curriculum order within each bucket."""
    order = adaptive_bucket_order(user_name)
    rank = {bucket: i for i, bucket in enumerate(order)}
    return sorted(problems, key=lambda p: rank.get(difficulty_bucket(p["difficulty"]), 99))


# --- submissions: per-problem attempt history, for a real "your submissions" view ---

def record_submission(problem_id, code, passed, total, error=None, user_name="you"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO submissions (problem_id, user_name, code, passed, total, error) VALUES (?, ?, ?, ?, ?, ?)",
        (problem_id, user_name, code, passed, total, error),
    )
    conn.commit()
    conn.close()


def get_submissions(problem_id, user_name=None, limit=10):
    conn = get_conn()
    if user_name:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE problem_id = ? AND user_name = ? ORDER BY id DESC LIMIT ?",
            (problem_id, user_name, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE problem_id = ? ORDER BY id DESC LIMIT ?",
            (problem_id, limit),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def last_submission_code(problem_id, user_name="you"):
    """The most recent code this user personally submitted for this problem,
    if any — used to restore their in-progress attempt instead of a blank
    starter."""
    conn = get_conn()
    row = conn.execute(
        "SELECT code FROM submissions WHERE problem_id = ? AND user_name = ? ORDER BY id DESC LIMIT 1",
        (problem_id, user_name),
    ).fetchone()
    conn.close()
    return row["code"] if row else None
