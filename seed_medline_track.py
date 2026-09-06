#!/usr/bin/env python3
"""Install the Medline track into curriculum.db from medline_track.json.

Every problem is verified before it is written: its reference solution is
executed against its own tests through the same grading path a learner's
submission takes, and anything that doesn't pass is rejected rather than
shipped. Same rule the Ollama generator lives under — nothing unverified
reaches a learner.

    python seed_medline_track.py --dry-run    # verify only, touch nothing
    python seed_medline_track.py              # verify, then install

Re-runnable: problems are written with INSERT OR REPLACE, so fixing a
problem in the JSON and re-running updates it in place. Nobody's progress
is touched.
"""

import argparse
import inspect
import json
import sqlite3
import sys
import time
from pathlib import Path

import db
import grading

TRACK_FILE = Path(__file__).parent / "medline_track.json"

# The track is meant to be what a new cohort member sees FIRST. Existing
# problems occupy sort_order 1..N, so the track sits below them on a number
# line that all_problems() orders by — without renumbering anything already
# in the database.
SORT_BASE = -1000


def arity_error(spec):
    """Catches the flattened-list-argument mistake with a readable message
    instead of letting it surface as a silent test failure."""
    ns = {}
    try:
        exec(spec["reference_solution"], ns)
    except Exception as e:
        return f"reference solution raised on load: {e!r}"
    func = ns.get(spec["func_name"])
    if func is None:
        return f"reference solution never defines {spec['func_name']}()"
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return None
    if any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params.values()):
        return None
    required = [p for p in params.values() if p.default is inspect.Parameter.empty]
    for i, test in enumerate(spec["tests"], start=1):
        n = len(test["args"])
        if n < len(required) or n > len(params):
            return (f"test {i} passes {n} argument(s) but {spec['func_name']}() takes "
                    f"{len(params)} — a list parameter was probably flattened across slots")
    return None


def verify(spec):
    """Returns None if the problem is sound, or a human-readable reason it isn't."""
    if not spec.get("tests"):
        return "no tests"

    for field in ("reference_solution", "starter"):
        denied = grading.code_is_denied(spec.get(field, ""))
        if denied:
            return f"{field} contains denylisted token `{denied}` — a learner could never submit this"

    problem = {
        "func_name": spec["func_name"],
        "tests": [{"type": "args", "args": t["args"], "expected": t["expected"]}
                  for t in spec["tests"]],
    }

    err = arity_error(spec)
    if err:
        return err

    passed, total, err = grading.exec_and_test(problem, spec["reference_solution"])
    if err:
        return err
    if passed != total:
        return f"reference solution passes only {passed}/{total} of its own tests"
    return None


def add_with_retry(**kwargs):
    """grow_curriculum.py may be writing to the same SQLite file."""
    for attempt in range(6):
        try:
            db.add_problem(**kwargs)
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == 5:
                raise
            time.sleep(2 ** attempt * 0.25)


def main():
    parser = argparse.ArgumentParser(description="Install the Medline track")
    parser.add_argument("--dry-run", action="store_true",
                        help="verify every problem but write nothing")
    args = parser.parse_args()

    if not TRACK_FILE.exists():
        print(f"No track file at {TRACK_FILE}")
        return 1

    track = json.loads(TRACK_FILE.read_text(encoding="utf-8"))
    categories = track["categories"]

    db.init_db()

    accepted, rejected = [], []
    order = SORT_BASE

    for cat_index, cat in enumerate(categories, start=1):
        for spec in cat["problems"]:
            reason = verify(spec)
            if reason:
                rejected.append((spec.get("id", "?"), spec.get("title", "?"), reason))
                continue
            accepted.append((cat["name"], spec, order))
            order += 1

    print(f"Verified {len(accepted) + len(rejected)} problems: "
          f"{len(accepted)} sound, {len(rejected)} rejected.\n")

    if rejected:
        print("REJECTED — not installed:")
        for pid, title, reason in rejected:
            print(f"  [{pid}] {title}\n      {reason}")
        print()

    if args.dry_run:
        print("Dry run — nothing written.")
        return 0

    for cat_index, cat in enumerate(categories, start=1):
        db.get_or_create_category(cat["name"], sort_order=cat_index)

    for cat_name, spec, sort_order in accepted:
        add_with_retry(
            id=spec["id"],
            category=cat_name,
            title=spec["title"],
            difficulty=int(spec["difficulty"]),
            prompt=spec["prompt"],
            func_name=spec["func_name"],
            starter=spec["starter"],
            tests=[{"type": "args", "args": t["args"], "expected": t["expected"]}
                   for t in spec["tests"]],
            hint=spec.get("hint"),
            lesson=spec.get("lesson"),
            source="curated",
            sort_order=sort_order,
        )

    print(f"Installed {len(accepted)} problems.\n")
    print("Track contents now in the database:")
    for cat in categories:
        n = len(db.all_problems(category=cat["name"]))
        print(f"  {cat['name']}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
