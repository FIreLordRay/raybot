# Raybot Agent

A local, tool-using Python tutor. It runs on your own machine against Ollama —
no API key, no request leaves the box.

The difference between this and a chat box: it **calls tools instead of guessing**.
Ask what `sum(range(11))` evaluates to and it executes the code and reports `55`.
Ask how you're doing and it reads the real curriculum database. Every tool call is
shown in the transcript with the exact arguments and the raw result.

```
python run_agent.py     ->  http://localhost:5003
```

## Requirements

- Ollama running locally with a **tool-capable** model:
  ```
  ollama pull llama3.2:3b
  ```
  Model capability matters here. `llama3.2:3b` advertises `tools`; plain `llama3`
  does not, which is why this uses the smaller model even though the curriculum
  generator elsewhere uses the larger one.
- Flask.
- The curriculum tools additionally need the `python-practice` folder and
  `curriculum.db`. Without them the agent still runs — those tools just drop out
  of the toolset rather than being advertised and then failing.

## Layout

| File | Role |
|---|---|
| `agent.py` | The loop: call Ollama, run any requested tools, feed results back, repeat until it answers. Web-framework free. |
| `tools.py` | Tool implementations plus their JSON schemas. |
| `web.py` | Flask UI. Stateless — the browser holds the conversation and posts it each turn. |
| `../run_agent.py` | Entry point. |

`agent.run_turn()` has no Flask dependency, so the loop is usable on its own:

```python
from raybot_agent import run_turn

reply, trace, history = run_turn([], "What does 2**20 come to?")
print(trace)   # [{'tool': 'run_python', 'args': {...}, 'result': '1048576'}]
print(reply)
```

## Tools

| Tool | What it does |
|---|---|
| `run_python` | Executes a snippet and returns its output. A bare final expression is reported too, so `sum(range(11))` answers without needing `print()`. |
| `get_progress` | Solved count, percentage, streak, quizzes taken. |
| `list_categories` | Every category with its solved/total. |
| `find_problem` | Search problems by title or category. |

## About `run_python` — read this

It executes code the **model** chose, which is a different risk profile from
executing code a person typed.

- A denylist screens the obvious things (file, network, process, `exec`/`eval`
  re-entry). It mirrors `python-practice/grading.py` deliberately.
- A 5-second timeout bounds how long the request *waits*.

Two honest limitations:

1. **It is not a sandbox.** The denylist is a best-effort screen, not containment.
2. **The timeout doesn't stop the code.** Python can't kill a thread, so a runaway
   loop keeps burning a core as a daemon thread until the process exits. Fine for a
   local single-user tool; not fine as a shared service.

Run it on localhost, for yourself. Don't expose it.

## Design notes

- **Stateless server.** History lives in the browser and is posted each turn,
  capped at 20 messages to keep the prompt bounded on a small local model.
- **Tool loop is capped** at 5 round-trips, and the tools are withheld on the final
  step so the model has to produce prose instead of looping forever.
- **Tool failures don't kill the turn** — the error text is handed back to the model
  as the tool result, so it can recover or explain.
- **Graceful degradation** everywhere: no Ollama gives a clear message and a red
  status dot rather than a stack trace; no database hides the curriculum tools.
