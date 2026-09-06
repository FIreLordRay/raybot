"""Shared Ollama-backed tutoring: general Q&A and automatic wrong-answer explanations.

Used by both the Discord bot (!firelordray) and the dashboard's interactive quiz,
so both surfaces explain things the same way. Requires a local Ollama server
(https://ollama.com) running with a model pulled — see OLLAMA_MODEL below.
"""

import json
import random
import urllib.error
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/chat"
# Fast model for prose (Q&A, explanations, lessons) — fits entirely on a 6GB GPU
# (llama3:8b spills 6/33 layers to CPU on this hardware, which is the difference
# between this and multi-minute responses). Structured problem generation uses
# its own, more reliable model — see generator.GENERATION_MODEL.
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_TIMEOUT_SECONDS = 60

RAYBOT_SYSTEM_PROMPT = (
    "You are Raybot — a warm, upbeat coding tutor with genuine personality, not a generic "
    "assistant. You're genuinely excited about Python and about this person's progress, and "
    "it shows in your tone: encouraging, a little playful, never condescending. "
    "Explain concepts clearly and simply, with short examples when useful. "
    "If they're stuck on a specific exercise, help them understand the underlying concept "
    "so they can write the solution themselves — don't just hand them the finished code "
    "unless they explicitly ask you to. Keep answers concise — a couple of short paragraphs "
    "at most, not a lecture."
)

PRAISE = [
    "Correct! 🎉",
    "Nailed it! 💪",
    "Boom — that's the one. 🚀",
    "Clean solve, nice work. ✨",
    "Yes! Exactly right.",
    "Solid. On to the next one. 🔥",
    "That'll do it! Nicely solved.",
    "Ship it — that's correct!",
]


def praise():
    """A varied congratulatory phrase for a correct answer — no LLM call, just
    keeps the same-old 'Correct!' from getting stale after the hundredth solve."""
    return random.choice(PRAISE)


def _chat(system_prompt, user_message, json_mode=False, timeout=None, model=None):
    body = {
        "model": model or OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
    }
    if json_mode:
        body["format"] = "json"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or OLLAMA_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("message", {}).get("content") or "(Ollama returned an empty response.)"
    except urllib.error.URLError as e:
        return f"Couldn't reach Ollama at {OLLAMA_URL} — make sure it's running ({e.reason})."
    except Exception as e:
        return f"Error talking to Ollama: {e!r}"


def chat(system_prompt, user_message, json_mode=False, timeout=None, model=None):
    """Public entry point for other modules (e.g. generator.py) that need raw
    chat access — same transport as ask_raybot()/explain_failure(), optionally
    requesting Ollama's JSON output mode or a specific model (defaults to
    OLLAMA_MODEL, the fast prose model — pass model= to use a different one,
    e.g. generator.py's more reliable structured-generation model)."""
    return _chat(system_prompt, user_message, json_mode=json_mode, timeout=timeout, model=model)


def is_error_response(text):
    """True if `text` is one of _chat()'s own connection/timeout error strings,
    rather than a real model response."""
    return text.startswith("Couldn't reach Ollama") or text.startswith("Error talking to Ollama")


def ask_raybot(question, problem=None):
    """General Q&A, optionally grounded in the exercise the person is currently on."""
    system_prompt = RAYBOT_SYSTEM_PROMPT
    if problem:
        system_prompt += (
            f"\n\nThey're currently working on this exercise:\n"
            f"Title: {problem['title']}\n"
            f"Prompt: {problem['prompt']}"
        )
    return _chat(system_prompt, question)


def generate_lesson(title, prompt, topic):
    """A short Exercism-style concept lesson to show before the exercise itself —
    teaches the underlying idea, not the specific solution."""
    user_message = (
        f"Write a short lesson (3-5 sentences, plain text, no headings or code fences) "
        f"introducing the Python concept behind this exercise, for a beginner. "
        f"Explain the general idea and maybe one tiny inline example, but do NOT solve "
        f"the exercise itself or write the exercise's solution.\n\n"
        f"Topic area: {topic}\n"
        f"Exercise title: {title}\n"
        f"Exercise prompt: {prompt}"
    )
    return _chat(RAYBOT_SYSTEM_PROMPT, user_message)


def explain_failure(problem, code, passed, total):
    """Auto-generated explanation of why a submission failed its tests.

    Deliberately does not reveal the corrected code — the point is to nudge
    understanding, not to solve it for them.
    """
    user_message = (
        f"I'm working on this exercise:\n"
        f"Title: {problem['title']}\n"
        f"Prompt: {problem['prompt']}\n\n"
        f"Here's my code:\n```python\n{code}\n```\n\n"
        f"It passed {passed} out of {total} tests. Without giving me the corrected code, "
        f"explain in 2-4 short sentences what's likely wrong with my logic and what I should "
        f"think about to fix it."
    )
    return _chat(RAYBOT_SYSTEM_PROMPT, user_message)
