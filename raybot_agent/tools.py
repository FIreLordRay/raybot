"""Tool registry for the Raybot agent.

Each tool is a plain Python function plus an Ollama/OpenAI-style JSON schema.
`SPECS` is what gets sent to the model; `REGISTRY` is what actually runs.

Tools that need the practice curriculum degrade gracefully: if the
python-practice folder or curriculum.db isn't present, those tools drop out of
SPECS entirely rather than being advertised to the model and then failing.
"""

import ast
import contextlib
import io
import re
import sys
import threading
from pathlib import Path

# --- optional curriculum backend -------------------------------------------------
PRACTICE_DIR = Path(r"C:\Users\rayra\Documents\python-practice")

db = None
try:
    if PRACTICE_DIR.exists():
        sys.path.insert(0, str(PRACTICE_DIR))
        import db  # noqa: E402
except Exception:  # pragma: no cover - environment dependent
    db = None


# --- run_python ------------------------------------------------------------------
# Mirrors the denylist in python-practice/grading.py on purpose: there should be
# one shared idea of "obviously dangerous", and this project is standalone so it
# carries its own copy rather than importing it.
#
# This is NOT a sandbox. It is a best-effort screen plus a timeout, and here the
# *model* chooses what to run, so treat it accordingly: localhost only, and only
# with a model you're willing to let execute code on your machine.
DENYLIST = [
    "import os", "import sys", "import subprocess", "import socket", "import shutil",
    "import requests", "import urllib", "import ctypes", "import pathlib",
    "__import__", "open(", "eval(", "exec(", "globals(", "locals(",
    "input(", "compile(", "breakpoint(",
]

RUN_TIMEOUT_SECONDS = 5
MAX_OUTPUT_CHARS = 2000

# Caveat worth knowing: the timeout bounds how long we *wait*, not how long the
# snippet runs. Python can't kill a thread, so a runaway loop keeps burning a core
# (as a daemon thread) until the process exits. Fine for a local single-user tool;
# it would not be fine as a shared service.


def _denied(code):
    lowered = code.lower()
    for token in DENYLIST:
        if token in lowered:
            return token
    return None


def run_python(code=""):
    """Execute a short Python snippet and return whatever it printed.

    If the final line is a bare expression, its value is reported too, so
    `sum(range(11))` gives an answer without the model having to remember print().
    """
    if not code.strip():
        return "No code supplied."

    blocked = _denied(code)
    if blocked:
        return f"Refused: the snippet contains a blocked pattern ({blocked!r})."

    result = {}

    def target():
        buffer = io.StringIO()
        namespace = {}
        try:
            # Split on the AST, not on lines: splitting text would break any snippet
            # whose last line sits inside an indented block.
            tree = ast.parse(code)
            tail = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                tail = ast.Expression(tree.body.pop().value)

            with contextlib.redirect_stdout(buffer):
                if tree.body:
                    exec(compile(tree, "<agent>", "exec"), namespace)  # noqa: S102
                if tail is not None:
                    value = eval(compile(tail, "<agent>", "eval"), namespace)  # noqa: S307
                    if value is not None:
                        print(repr(value))
            result["output"] = buffer.getvalue()
        except Exception as exc:
            result["output"] = buffer.getvalue()
            result["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(RUN_TIMEOUT_SECONDS)

    if thread.is_alive():
        return f"Timed out after {RUN_TIMEOUT_SECONDS}s (the snippet was still running)."

    output = (result.get("output") or "").strip()
    error = result.get("error")
    if error and output:
        return f"{output}\n{error}"[:MAX_OUTPUT_CHARS]
    if error:
        return error[:MAX_OUTPUT_CHARS]
    return (output or "(ran successfully, no output)")[:MAX_OUTPUT_CHARS]


# --- curriculum reads -------------------------------------------------------------
def get_progress():
    """Overall curriculum progress: solved count, percentage, streak, quizzes taken."""
    if db is None:
        return "The curriculum database isn't available in this environment."
    total = len(db.all_problems())
    solved = len(db.get_solved_ids())
    pct = round((solved / total) * 100, 1) if total else 0
    streak = db.get_streak()
    quizzes = len(db.get_quiz_history())
    return (f"{solved} of {total} problems solved ({pct}% of the curriculum). "
            f"Current streak: {streak} day(s). Quizzes taken: {quizzes}.")


def list_categories():
    """Every category with how many of its problems are solved."""
    if db is None:
        return "The curriculum database isn't available in this environment."
    rows = db.category_stats()  # [{"name", "solved", "total", "pct"}, ...]
    if not rows:
        return "No categories found."
    return "; ".join(f"{r['name']}: {r['solved']}/{r['total']}" for r in rows)


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "problem",
              "problems", "exercise", "exercises", "difficulty"}


def find_problem(query=""):
    """Search curriculum problems by title or category.

    Token-scored rather than substring-matched: models tend to pass composite
    queries like "fizzbuzz list (loops)", which no single substring matches. Each
    query word that hits a title or category scores the problem, best first.
    """
    if db is None:
        return "The curriculum database isn't available in this environment."
    if not query.strip():
        return "Provide something to search for."

    words = [w for w in _WORD_RE.findall(query.lower()) if w not in _STOPWORDS]
    if not words:
        return f"No problems matched {query!r}."

    scored = []
    for problem in db.all_problems():
        title = problem.get("title") or ""
        category = problem.get("category") or ""
        haystack = f"{title} {category}".lower()
        score = sum(1 for w in words if w in haystack)
        if score:
            scored.append(
                (score, f"{title} ({category}, difficulty {problem.get('difficulty')})")
            )

    if not scored:
        return f"No problems matched {query!r}."
    scored.sort(key=lambda pair: -pair[0])
    return "; ".join(label for _, label in scored[:8])


# --- registry ---------------------------------------------------------------------
def _spec(name, description, properties=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


_ALL = [
    (
        run_python,
        _spec(
            "run_python",
            "Execute a short Python snippet and return its output. Use this to check an "
            "answer, demonstrate a concept, or compute something rather than guessing.",
            {"code": {"type": "string", "description": "The Python source to run."}},
            ["code"],
        ),
        False,
    ),
    (
        get_progress,
        _spec(
            "get_progress",
            "Look up the learner's overall curriculum progress, streak and quiz count.",
        ),
        True,
    ),
    (
        list_categories,
        _spec(
            "list_categories",
            "List every curriculum category with how many of its problems are solved.",
        ),
        True,
    ),
    (
        find_problem,
        _spec(
            "find_problem",
            "Search the curriculum for problems whose title or category matches a query.",
            {"query": {"type": "string", "description": "Text to search for."}},
            ["query"],
        ),
        True,
    ),
]

REGISTRY = {}
SPECS = []
for _func, _schema, _needs_db in _ALL:
    if _needs_db and db is None:
        continue
    REGISTRY[_schema["function"]["name"]] = _func
    SPECS.append(_schema)

CURRICULUM_AVAILABLE = db is not None
