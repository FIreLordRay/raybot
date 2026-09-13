"""The Raybot agent loop: chat with Ollama, let it call tools, feed results back.

Deliberately dependency-free (urllib, not requests) so the package can be dropped
into any environment that has a local Ollama running.

The loop is the standard shape:
    send messages + tool specs -> model either answers or asks for tool calls
    -> run the tools -> append results as role="tool" -> send again
until the model answers in prose or MAX_STEPS is hit.
"""

import json
import re
import urllib.error
import urllib.request

from . import tools as toolkit

OLLAMA_URL = "http://localhost:11434/api/chat"

# Must be a model with the "tools" capability. llama3.2:3b has it; plain llama3
# does not, which is why the generator elsewhere in this project uses llama3 but
# the agent can't.
MODEL = "llama3.2:3b"
TIMEOUT_SECONDS = 120

# How many tool round-trips before we stop and make it answer with what it has.
MAX_STEPS = 5

SYSTEM_PROMPT = (
    "You are Raybot — a warm, upbeat Python tutor with genuine personality, not a generic "
    "assistant. You are talking to someone working through a Python curriculum.\n\n"
    "You have tools. Use them for facts you'd otherwise be guessing at:\n"
    "- To compute or check something concrete — what an expression evaluates to, whether "
    "a snippet works — call run_python and report the real result.\n"
    "- For their progress, streak, categories or which problems exist, call the matching "
    "curriculum tool rather than inventing numbers.\n\n"
    "Do NOT call a tool to answer a conceptual question. If they ask what an idea means, "
    "how something works, or what approach to take, just explain it directly — no tool.\n"
    "Keep any code you run short; never paste a full solution into run_python.\n\n"
    "Never state a number or an execution result you haven't actually obtained from a tool. "
    "Teach the concept so they can write the solution themselves — don't hand over finished "
    "answers unless they ask. Keep replies to a couple of short paragraphs."
)


class OllamaUnavailable(RuntimeError):
    """Raised when the local Ollama server can't be reached."""


def _post(payload):
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        raise OllamaUnavailable(
            f"Couldn't reach Ollama at {OLLAMA_URL} ({exc}). Is `ollama serve` running, "
            f"and have you pulled {MODEL}?"
        ) from exc


def _arguments(call):
    """Ollama returns parsed dict arguments; some builds return a JSON string."""
    raw = call.get("function", {}).get("arguments", {})
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _declared_params(name):
    for spec in toolkit.SPECS:
        if spec["function"]["name"] == name:
            return list(spec["function"]["parameters"].get("properties", {}))
    return []


def _adapt_args(name, args):
    """Reconcile the model's argument names with the tool's actual signature.

    Small models invent plausible-but-wrong keys (`topic=` for a tool that takes
    `query=`), which would otherwise fail as a TypeError and waste a round-trip.
    Unknown keys are dropped; a single stray value is mapped onto a single
    expected parameter.
    """
    declared = _declared_params(name)
    if not declared:
        return {}

    kept = {k: v for k, v in args.items() if k in declared}
    if kept:
        return kept

    # Nothing matched. If the tool takes one parameter and the model supplied one
    # value, it almost certainly meant that.
    values = [v for v in args.values() if isinstance(v, (str, int, float))]
    if len(declared) == 1 and len(values) == 1:
        return {declared[0]: values[0]}
    return {}


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _looks_like_tool_json(text):
    """True if the model is clearly *trying* to call a tool in plain text."""
    stripped = _FENCE_RE.sub(r"\1", (text or "").strip()).strip()
    return stripped.startswith("{") and '"name"' in stripped


def _salvage_tool_call(text):
    """Recover a tool call the model wrote as content instead of using tool_calls.

    Small local models intermittently emit {"name": ..., "parameters": {...}} as
    prose. Without this the raw JSON is shown to the user as the answer, which
    looks broken. Returns a normalised call dict, or None if it isn't one.
    """
    stripped = _FENCE_RE.sub(r"\1", (text or "").strip()).strip()
    if not stripped.startswith("{"):
        return None
    try:
        blob = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(blob, dict):
        return None

    name = blob.get("name")
    args = blob.get("parameters", blob.get("arguments", {}))
    if name in toolkit.REGISTRY and isinstance(args, dict):
        return {"function": {"name": name, "arguments": args}}
    return None


def run_turn(history, user_message):
    """Run one user turn to completion.

    `history` is a list of prior {"role", "content"} messages (no tool plumbing).
    Returns (reply_text, trace, new_history) where trace is a list of
    {"tool", "args", "result"} describing every tool the model actually invoked.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    trace = []
    nudged = False  # we only re-ask for a malformed tool call once

    for step in range(MAX_STEPS):
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
        }
        # On the final permitted step, drop the tools so the model has to answer.
        if step < MAX_STEPS - 1:
            payload["tools"] = toolkit.SPECS

        message = _post(payload).get("message", {}) or {}
        calls = message.get("tool_calls") or []
        content = (message.get("content") or "").strip()

        # The model sometimes writes a tool call as content instead of using the
        # tool_calls field. Treat that as the call it meant to make.
        if not calls:
            salvaged = _salvage_tool_call(content)
            if salvaged is not None:
                calls = [salvaged]
                message = {"role": "assistant", "content": "", "tool_calls": calls}

        if not calls and _looks_like_tool_json(content) and not nudged:
            # Tool-call-shaped but unparseable (typically unescaped quotes inside the
            # code string). Don't try to repair JSON into something executable — ask
            # for it again, once, and let it answer in prose if it can't manage.
            nudged = True
            messages.append({"role": "user", "content": (
                "That wasn't a valid tool call. Either call the tool properly, or just "
                "answer in plain prose without using a tool."
            )})
            continue

        if not calls:
            reply = content
            if _looks_like_tool_json(reply):
                # Never show raw JSON to the user as an answer.
                reply = "I garbled a tool call there — ask me again?"
            if not reply:
                reply = "I didn't manage to put an answer together there — try rephrasing?"
            new_history = history + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            ]
            return reply, trace, new_history

        # The model wants tools. Record the call, run it, feed the result back.
        messages.append(message)
        for call in calls:
            name = call.get("function", {}).get("name", "")
            args = _adapt_args(name, _arguments(call))
            func = toolkit.REGISTRY.get(name)

            if func is None:
                result = f"No such tool: {name!r}."
            else:
                try:
                    result = str(func(**args))
                except TypeError as exc:
                    result = f"Bad arguments for {name}: {exc}"
                except Exception as exc:  # a tool blowing up shouldn't kill the turn
                    result = f"{name} failed: {type(exc).__name__}: {exc}"

            trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "name": name, "content": result})

    # Ran out of steps without a prose answer.
    reply = "I went back and forth with my tools too many times there — ask me again?"
    new_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": reply},
    ]
    return reply, trace, new_history
