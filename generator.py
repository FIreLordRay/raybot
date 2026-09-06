"""Ollama-backed problem generator: creates new curriculum problems on demand.

Every generated problem is verified before it's ever added to the database —
the model also writes a reference solution, and that solution has to actually
pass the generated tests (and clear the same denylist real submissions do)
or the whole thing is discarded and regenerated. Bad auto-generated content
should never reach a learner.
"""

import inspect
import json
import random
import re

import db
import grading
import tutor

MAX_ATTEMPTS = 3
BUCKET_DIFFICULTY = {"easy": 1, "medium": 2, "hard": 3}

# Structured-JSON generation (a title + prompt + tests + a reference solution
# that all have to agree with each other) needs a more reliable model than the
# fast prose model tutor.py defaults to (llama3.2:3b, tried here, produced
# malformed Python and mismatched test arg counts far more often). llama3
# is slower per attempt but verifies successfully often enough to be the
# better net choice for this specific task.
GENERATION_MODEL = "llama3"

GENERATOR_SYSTEM_PROMPT = (
    "You are generating a single Python coding exercise for a beginner-to-intermediate "
    "practice curriculum. Respond with ONLY a JSON object, no prose, no markdown fences, "
    "matching exactly this shape:\n"
    '{"title": "...", "prompt": "...", "func_name": "...", "starter": "...", '
    '"tests": [{"args": [...], "expected": ...}, ...], "reference_solution": "..."}\n\n'
    "Rules:\n"
    "- func_name must be a valid Python identifier (snake_case).\n"
    "- starter must define func_name with a `pass` body, as a single string with real "
    "newlines written as \\n.\n"
    "- reference_solution must be a complete, correct implementation of func_name as a "
    "single string (same format as starter, but with a working body instead of pass).\n"
    "- tests must have at least 3 and at most 6 cases. args is a JSON array with EXACTLY ONE element "
    "PER PARAMETER of func_name, in order — expected is the exact return value.\n"
    "- CRITICAL: if a parameter's own value is itself a list, that list is still just ONE element of "
    "args, not flattened into several. Example: for `def find_max(items, k)` where items is a list, "
    "args must be [[3, 7, 1], 2] (2 elements — one per parameter, the first of which happens to be a "
    "list) — never [3, 7, 1, 2] (wrong: that's 4 elements for a 2-parameter function). Before writing "
    "each test, count func_name's parameters and confirm args has exactly that many elements.\n"
    "- Only use JSON-safe values in tests (numbers, strings, booleans, null, arrays, objects) — "
    "no tuples, no custom objects.\n"
    "- Do not use input(), file I/O, network access, or randomness in the solution.\n"
    "- Compute every `expected` value carefully and exactly by mentally running reference_solution "
    "on that test's args, step by step, before writing it down — a wrong expected value is the single "
    "most common reason this gets rejected.\n"
)


def _slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "problem"


def _unique_id(base):
    existing = {p["id"] for p in db.all_problems()}
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _parse_json_object(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def _verify(spec):
    """Runs the reference solution against the generated tests. Returns (ok, detail)."""
    required = {"title", "prompt", "func_name", "starter", "tests", "reference_solution"}
    missing = required - spec.keys()
    if missing:
        return False, f"missing fields: {missing}"
    if not isinstance(spec["tests"], list) or not (3 <= len(spec["tests"]) <= 6):
        return False, "tests must be a list of 3-6 cases"
    for t in spec["tests"]:
        if not isinstance(t, dict) or "args" not in t or "expected" not in t:
            return False, "each test needs args and expected"

    denied = grading.code_is_denied(spec["reference_solution"])
    if denied:
        return False, f"reference solution contains denylisted pattern `{denied}`"

    # Catch the most common structural mistake explicitly, with a precise
    # message — an args list flattened instead of matching the function's
    # actual parameter count (e.g. a list-typed parameter's contents spilled
    # into extra top-level args elements).
    try:
        ns = {}
        exec(spec["reference_solution"], ns)
        func = ns.get(spec["func_name"])
        if func is None:
            return False, f"reference_solution doesn't define a function named {spec['func_name']}"
        param_count = len(inspect.signature(func).parameters)
    except Exception as e:
        return False, f"reference solution failed to load: {e!r}"

    # Auto-repair the single most common model mistake: for a one-parameter
    # function, flattening that parameter's list value across several `args`
    # slots instead of nesting it as args == [the_list]. Only safe to guess
    # for exactly one parameter — with two+ parameters there's no reliable way
    # to know which flattened values were meant to form the list.
    if param_count == 1:
        for t in spec["tests"]:
            if isinstance(t["args"], list) and len(t["args"]) != 1:
                t["args"] = [t["args"]]

    for i, t in enumerate(spec["tests"]):
        if not isinstance(t["args"], list) or len(t["args"]) != param_count:
            got = len(t["args"]) if isinstance(t["args"], list) else "non-list"
            return False, (
                f"test {i} has {got} args but {spec['func_name']} takes {param_count} parameter(s) — "
                f"if a parameter is itself a list, nest it as one element of args instead of flattening it"
            )

    fake_problem = {
        "func_name": spec["func_name"],
        "tests": [{"type": "args", "args": t["args"], "expected": t["expected"]} for t in spec["tests"]],
    }
    passed, total, error = grading.exec_and_test(fake_problem, spec["reference_solution"])
    if error:
        return False, error
    if passed != total:
        return False, f"reference solution only passed {passed}/{total} of its own tests"
    return True, None


def generate_problem(category, bucket="medium", max_attempts=MAX_ATTEMPTS):
    """Generates, verifies, and stores one new problem in `category` at the given
    difficulty bucket ("easy"/"medium"/"hard"). Returns the new problem's id on
    success, or None if every attempt failed verification.
    """
    difficulty = BUCKET_DIFFICULTY.get(bucket, 2)

    existing_titles = [p["title"] for p in db.all_problems(category=category)]
    if len(existing_titles) > 25:
        existing_titles = random.sample(existing_titles, 25)
    avoid_note = ""
    if existing_titles:
        avoid_note = (
            "\n\nThis category already has these exercises — do not duplicate any of them or "
            "generate a near-variant of one:\n" + "\n".join(f"- {t}" for t in existing_titles)
        )

    last_detail = None
    for _attempt in range(max_attempts):
        user_message = (
            f"Category/topic: {category}\n"
            f"Difficulty: {bucket}\n"
            f"Generate one new exercise now, distinct from the most common textbook examples."
            f"{avoid_note}"
        )
        if last_detail:
            user_message += (
                f"\n\nYour previous attempt was rejected for this reason: {last_detail}. "
                f"Try again, fixing that."
            )
        raw = tutor.chat(GENERATOR_SYSTEM_PROMPT, user_message, json_mode=True, timeout=90, model=GENERATION_MODEL)
        if tutor.is_error_response(raw):
            last_detail = raw
            continue
        try:
            spec = _parse_json_object(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_detail = f"invalid JSON: {e}"
            continue

        ok, detail = _verify(spec)
        if not ok:
            last_detail = detail
            continue

        problem_id = _unique_id(_slugify(spec["title"]))
        lesson = tutor.generate_lesson(spec["title"], spec["prompt"], category)
        if tutor.is_error_response(lesson):
            lesson = None

        db.add_problem(
            id=problem_id,
            category=category,
            title=spec["title"],
            difficulty=difficulty,
            prompt=spec["prompt"],
            func_name=spec["func_name"],
            starter=spec["starter"],
            tests=[{"type": "args", "args": t["args"], "expected": t["expected"]} for t in spec["tests"]],
            hint=None,
            lesson=lesson,
            is_class=False,
            source="generated",
        )
        return problem_id

    return None


def pick_category():
    """Picks the category with the fewest problems, to grow the curriculum evenly."""
    cats = db.get_categories()
    if not cats:
        return "basics"
    counts = [(c["name"], len(db.all_problems(category=c["name"]))) for c in cats]
    counts.sort(key=lambda x: x[1])
    return counts[0][0]


def next_or_generate(user_name=None, category=None, bucket="medium"):
    """Like db.next_unsolved()/db.next_unraced(), but generates a fresh
    problem instead of returning None when the pool is exhausted.

    Pass user_name for a personal pool (dashboard/!firelordray — problems
    *this user* hasn't solved yet). Leave it None for the shared race-mode
    pool (Discord !quiz — problems nobody has won a race with yet).

    Returns (problem_id_or_None, was_generated).
    """
    problem_id = db.next_unsolved(user_name, category=category) if user_name else db.next_unraced(category=category)
    if problem_id:
        return problem_id, False
    target_category = category or pick_category()
    generated_id = generate_problem(target_category, bucket=bucket)
    return generated_id, True
