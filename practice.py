#!/usr/bin/env python3
"""Daily Python practice generator — now backed by curriculum.db (SQLite),
with categories and an auto-growing curriculum (Ollama generates a new
problem whenever a category/difficulty runs out).

Commands:
    python practice.py today   [--category NAME]   Open today's problem in an editor, auto-test on save
    python practice.py test                         Run your solution against its test cases (one-off check)
    python practice.py hint                          Show a hint for the current problem
    python practice.py stats                         Show progress and streak
    python practice.py list    [--category NAME]     List the curriculum and your progress
    python practice.py categories                    List categories and per-category progress
    python practice.py skip                          Skip the current problem
    python practice.py more CATEGORY [easy|medium|hard]   Generate a new problem right now
"""

import argparse
import getpass
import subprocess
import sys
import time
from pathlib import Path

import db
import generator

ROOT = Path(__file__).parent
SOLUTIONS_DIR = ROOT / "solutions"

# Each person who uses the CLI on their own machine gets their own progress —
# their Windows login is a reasonable, zero-setup identity for that.
USER_NAME = getpass.getuser()

db.init_db()


def write_starter_file(problem):
    SOLUTIONS_DIR.mkdir(exist_ok=True)
    path = SOLUTIONS_DIR / f"{problem['id']}.py"
    if not path.exists():
        path.write_text(problem["starter"])
    return path


def open_editor(path):
    try:
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-t", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        print(f"Couldn't open an editor automatically — open this file yourself: {path}")


def show_problem(problem):
    print(f"=== {problem['title']} (difficulty {problem['difficulty']}, category: {problem['category']}) ===")
    if problem.get("lesson"):
        print()
        print(problem["lesson"])
    print()
    print(problem["prompt"])


def cmd_today(args, category):
    current_id = db.ensure_current(USER_NAME, category=category)
    if current_id is None:
        print(f"No more problems{' in ' + category if category else ''} — generating a new one with Ollama (may take a minute)...")
        current_id, _ = generator.next_or_generate(user_name=USER_NAME, category=category)
        db.set_current_id(USER_NAME, current_id)
    if current_id is None:
        print("Couldn't generate a new problem — check that Ollama is running (`ollama serve`).")
        return
    problem = db.problem_by_id(current_id)
    path = write_starter_file(problem)
    show_problem(problem)
    print()
    print("Opening it in Notepad now — write your solution there and save (Ctrl+S).")
    print("This window will automatically re-run the tests every time you save.")
    print("(Press Ctrl+C here to stop watching without solving it.)")
    print()
    open_editor(path)
    watch_and_test(problem, path)


def watch_and_test(problem, path):
    try:
        last_mtime = path.stat().st_mtime
    except FileNotFoundError:
        last_mtime = None
    try:
        while True:
            time.sleep(1)
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime == last_mtime:
                continue
            last_mtime = mtime
            time.sleep(0.2)  # let the editor finish writing the file
            print("Save detected — running tests...")
            solved_now = test_and_record(problem)
            print()
            if solved_now:
                return
    except KeyboardInterrupt:
        print("\nStopped watching. Run 'python practice.py today' any time to pick this back up.")


def import_solution(problem):
    path = SOLUTIONS_DIR / f"{problem['id']}.py"
    if not path.exists():
        print(f"No solution file found at {path}. Run 'python practice.py today' first.")
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"solution_{problem['id']}", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"Your solution raised an error while loading: {e!r}")
        return None
    return module


def run_tests_verbose(problem, module):
    tests = problem["tests"]
    passed = 0
    for i, spec in enumerate(tests, start=1):
        try:
            if spec.get("type") == "check":
                ns = {}
                exec(spec["code"], ns)
                ok = bool(ns["check"](module))
                detail = ""
            else:
                func = getattr(module, problem["func_name"])
                result = func(*spec["args"])
                ok = result == spec["expected"]
                detail = f" (got {result!r}, expected {spec['expected']!r})" if not ok else ""
        except Exception as e:
            ok = False
            detail = f" (raised {e!r})"
        print(f"  Test {i}: {'PASS' if ok else 'FAIL'}{detail}")
        if ok:
            passed += 1
    return passed, len(tests)


def test_and_record(problem):
    """Runs the problem's tests against the current solution file, updates progress,
    and prints results. Returns True if this run solved the problem."""
    module = import_solution(problem)
    if module is None:
        return False
    passed, total = run_tests_verbose(problem, module)
    attempts = db.increment_attempts(problem["id"], USER_NAME)
    if passed == total:
        db.mark_solved(problem["id"], user_name=USER_NAME, mode="cli")
        print(f"\nAll {total} tests passed! Problem solved in {attempts} attempt(s).")
        print(f"Current streak: {db.get_streak(USER_NAME)} day(s).")
        nxt = db.next_unsolved(USER_NAME)
        if nxt:
            print(f"Next up: {db.problem_by_id(nxt)['title']}. Run 'python practice.py today' to start it.")
        else:
            print("That was the last curated problem — 'today' will generate a new one next time.")
        return True
    else:
        print(f"\n{passed}/{total} tests passed. Keep going!")
        return False


def cmd_test(args, category):
    current_id = db.ensure_current(USER_NAME, category=category)
    if current_id is None:
        print("Nothing to test — run 'python practice.py today' first.")
        return
    problem = db.problem_by_id(current_id)
    print(f"Running tests for {problem['title']}...")
    test_and_record(problem)


def cmd_hint(args, category):
    current_id = db.ensure_current(USER_NAME, category=category)
    if current_id is None:
        print("No current problem.")
        return
    problem = db.problem_by_id(current_id)
    print(f"Hint for {problem['title']}: {problem['hint'] or '(no hint for this one — try Ask Raybot on the dashboard)'}")


def cmd_stats(args, category):
    total = len(db.all_problems())
    solved = len(db.get_solved_ids(USER_NAME))
    print(f"User: {USER_NAME}")
    print(f"Solved:  {solved}/{total}")
    print(f"Current streak: {db.get_streak(USER_NAME)} day(s)")
    print(f"Last solved: {db.get_last_solved_date(USER_NAME) or 'never'}")
    current_id = db.get_current_id(USER_NAME)
    if current_id:
        p = db.problem_by_id(current_id)
        if p:
            print(f"Current problem: {p['title']} ({db.get_attempts(current_id, USER_NAME)} attempt(s) so far)")
    print()
    for b in db.difficulty_bucket_stats(USER_NAME):
        print(f"  {b['name']}: {b['solved']}/{b['total']}")


def cmd_list(args, category):
    solved = db.get_solved_ids(USER_NAME)
    skipped = db.get_skipped_ids(USER_NAME)
    current = db.get_current_id(USER_NAME)
    for p in db.all_problems(category=category):
        if p["id"] in solved:
            mark = "[x]"
        elif p["id"] in skipped:
            mark = "[-]"
        elif p["id"] == current:
            mark = "[>]"
        else:
            mark = "[ ]"
        tag = " (generated)" if p["source"] == "generated" else ""
        print(f"{mark} L{p['difficulty']} {p['title']} ({p['category']}){tag}")


def cmd_categories(args, category):
    for c in db.category_stats(USER_NAME):
        print(f"{c['name']}: {c['solved']}/{c['total']} ({c['pct']}%)")


def cmd_skip(args, category):
    current_id = db.ensure_current(USER_NAME, category=category)
    if current_id is None:
        print("Nothing to skip.")
        return
    db.mark_skipped(current_id, USER_NAME)
    nxt = db.ensure_current(USER_NAME, category=category)
    if nxt:
        print(f"Skipped. Next up: {db.problem_by_id(nxt)['title']}. Run 'python practice.py today' to see it.")
    else:
        print("Skipped. Run 'python practice.py today' to generate the next one.")


def cmd_more(args, category):
    target = args.category_arg
    bucket = args.bucket_arg or "medium"
    print(f"Generating a new {bucket} problem for '{target}' with Ollama (may take a minute)...")
    problem_id = generator.generate_problem(target, bucket=bucket)
    if problem_id:
        print(f"Added: {db.problem_by_id(problem_id)['title']} ({problem_id})")
    else:
        print("Generation failed after a few attempts — check that Ollama is running and try again.")


def main():
    parser = argparse.ArgumentParser(description="Daily Python practice generator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_today = sub.add_parser("today", help="Show today's problem")
    p_today.add_argument("--category", default=None)

    p_test = sub.add_parser("test", help="Test your current solution")
    p_test.add_argument("--category", default=None)

    p_hint = sub.add_parser("hint", help="Show a hint for the current problem")
    p_hint.add_argument("--category", default=None)

    sub.add_parser("stats", help="Show progress and streak")

    p_list = sub.add_parser("list", help="List the curriculum")
    p_list.add_argument("--category", default=None)

    sub.add_parser("categories", help="List categories and per-category progress")

    p_skip = sub.add_parser("skip", help="Skip the current problem")
    p_skip.add_argument("--category", default=None)

    p_more = sub.add_parser("more", help="Generate a new problem in a category right now")
    p_more.add_argument("category_arg", metavar="CATEGORY")
    p_more.add_argument("bucket_arg", metavar="DIFFICULTY", nargs="?", default=None,
                         choices=["easy", "medium", "hard", None])

    args = parser.parse_args()

    commands = {
        "today": cmd_today,
        "test": cmd_test,
        "hint": cmd_hint,
        "stats": cmd_stats,
        "list": cmd_list,
        "categories": cmd_categories,
        "skip": cmd_skip,
        "more": cmd_more,
    }
    category = getattr(args, "category", None)
    commands[args.command](args, category)


if __name__ == "__main__":
    sys.exit(main())
