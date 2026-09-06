"""Shared code-grading helpers: extract a code block, screen it, and execute it
against a problem's tests. Used by both discord_raybot.py and dashboard.py so
there's exactly one place that knows how to safely (ish) run someone's answer.

Not a real sandbox — see the denylist note below. Callers are responsible for
bounding execution time (a thread-with-timeout, an executor, etc.) since this
module only does the exec + test-checking, not the timing.
"""

import re

CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n?(.*?)```", re.DOTALL)

ZERO_WIDTH_CHARS = [chr(0x200B), chr(0x200C), chr(0x200D), chr(0xFEFF)]
NBSP = chr(0x00A0)

# Best-effort screen against the most obviously dangerous patterns (file/network/
# process access, re-entering exec/eval). This is defense-in-depth, not a sandbox:
# someone determined could still work around it. Only trust this for people you
# actually trust to be in the room.
DENYLIST = [
    "import os", "import sys", "import subprocess", "import socket", "import shutil",
    "import requests", "import urllib", "import ctypes", "import pathlib",
    "__import__", "open(", "eval(", "exec(", "globals(", "locals(",
    "input(", "compile(", "breakpoint(",
]


def clean_code(code):
    """Strips invisible characters that break exec() but are indistinguishable
    from nothing when you're looking at the message (e.g. pasted from a chat
    app that quietly injects zero-width characters)."""
    for ch in ZERO_WIDTH_CHARS:
        code = code.replace(ch, "")
    return code.replace(NBSP, " ").strip()


def extract_code(message_content):
    """Pulls the contents of the first ```code block``` out of a message, or
    None if there isn't one. Cleans it before returning."""
    match = CODE_BLOCK_RE.search(message_content)
    if not match:
        return None
    return clean_code(match.group(1))


def code_is_denied(code):
    lowered = code.lower()
    for token in DENYLIST:
        if token in lowered:
            return token
    return None


class Namespace:
    """Minimal stand-in for a module, so run_tests() can use getattr() on it
    the same way it would on an imported solution file."""
    pass


def run_tests(problem, module):
    """Runs a problem's test specs against an exec'd/imported module-like object.

    Each spec is either:
      {"type": "args", "args": [...], "expected": <value>}   -- call func_name(*args) == expected
      {"type": "check", "code": "<source defining check(m)>"} -- exec the source, call check(module)

    Returns (passed, total).
    """
    tests = problem["tests"]
    passed = 0
    for spec in tests:
        try:
            if spec.get("type") == "check":
                ns = {}
                exec(spec["code"], ns)
                ok = bool(ns["check"](module))
            else:
                func = getattr(module, problem["func_name"])
                result = func(*spec["args"])
                ok = result == spec["expected"]
        except Exception:
            ok = False
        if ok:
            passed += 1
    return passed, len(tests)


def exec_and_test(problem, code):
    """Runs `code`, then grades it against the problem's tests.
    Synchronous and un-timed — the caller bounds time.

    Returns (passed, total, error_or_None).
    """
    denied = code_is_denied(code)
    if denied:
        return 0, len(problem["tests"]), f"Submission blocked (contains `{denied}`, not allowed here)."
    ns = Namespace()
    try:
        exec(code, ns.__dict__)
    except Exception as e:
        return 0, len(problem["tests"]), f"Error while running your code: {e!r}"
    try:
        passed, total = run_tests(problem, ns)
    except Exception as e:
        return 0, len(problem["tests"]), f"Error while testing: {e!r}"
    return passed, total, None
