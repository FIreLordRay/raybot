"""Flask front-end for the Raybot agent.

Stateless by design: the browser keeps the conversation and posts the whole
history each turn, so there's no session store and you can refresh without the
server caring.

    python run_agent.py     ->  http://localhost:5003
"""

import urllib.error
import urllib.request

from flask import Flask, jsonify, render_template_string, request

from . import agent
from . import tools as toolkit

app = Flask(__name__)

MAX_HISTORY_MESSAGES = 20  # keeps the prompt bounded on a small local model


def ollama_is_up():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Raybot Agent</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
  :root {
    --bg: #100c0a; --card: #191310; --card-border: #332a22;
    --text: #f7f2ec; --muted: #a09488;
    --accent: #f59e0b; --accent-dark: #d97706; --accent-soft: #2e2410;
    --ink: #0d0a08; --good: #34d399; --bad: #f87171;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; display: flex; flex-direction: column;
    background-color: var(--bg);
    background-image:
      radial-gradient(circle at center, rgba(255,255,255,0.05) 1px, transparent 1px),
      radial-gradient(ellipse 70% 60% at 4% 100%, rgba(245,158,11,0.22) 0%, transparent 82%),
      radial-gradient(ellipse 60% 55% at 100% 8%, rgba(244,63,94,0.16) 0%, transparent 82%),
      radial-gradient(circle at 50% 120%, #1a1210 0%, #100c0a 48%, #080605 100%);
    background-size: 4px 4px, auto, auto, auto;
    background-repeat: repeat, no-repeat, no-repeat, no-repeat;
    background-attachment: fixed;
    color: var(--text);
    font-family: "Plus Jakarta Sans", -apple-system, "Segoe UI", Arial, sans-serif;
    line-height: 1.6; -webkit-font-smoothing: antialiased;
  }
  ::selection { background: var(--accent); color: #1a1002; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  ::-webkit-scrollbar { width: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--card-border); border-radius: 999px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--accent-dark); }

  header {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 14px 22px; border-bottom: 1px solid var(--card-border);
    background: rgba(25,19,16,0.55); backdrop-filter: blur(12px);
  }
  .brand { display: flex; align-items: center; gap: 9px; font-weight: 800; letter-spacing: -0.02em; }
  .brand .bot { font-size: 1.35rem; animation: float 2.4s ease-in-out infinite; }
  @keyframes float { 0%,100% { transform: translateY(0) rotate(0); } 25% { transform: translateY(-3px) rotate(-8deg); } 75% { transform: translateY(-2px) rotate(8deg); } }
  .chip { border: 1px solid var(--card-border); background: rgba(13,10,8,0.6); color: var(--muted);
          border-radius: 999px; padding: 4px 12px; font-size: 0.74rem; font-weight: 600;
          font-family: Consolas, monospace; }
  .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: middle; }
  .dot.up { background: var(--good); box-shadow: 0 0 7px var(--good); }
  .dot.down { background: var(--bad); box-shadow: 0 0 7px var(--bad); }
  header .spacer { flex: 1; }

  .shell { flex: 1; display: grid; grid-template-columns: 260px 1fr; min-height: 0; }
  @media (max-width: 860px) { .shell { grid-template-columns: 1fr; } aside { display: none; } }

  aside { border-right: 1px solid var(--card-border); padding: 20px; overflow-y: auto; }
  aside h2 { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em;
             color: var(--accent); margin: 0 0 12px; font-weight: 800; }
  .tool { border: 1px solid var(--card-border); background: rgba(25,19,16,0.5);
          border-radius: 12px; padding: 11px 13px; margin-bottom: 9px; }
  .tool code { font-family: Consolas, monospace; font-size: 0.78rem; color: var(--accent); font-weight: 700; }
  .tool p { margin: 5px 0 0; font-size: 0.76rem; color: var(--muted); line-height: 1.45; }
  aside .note { font-size: 0.72rem; color: var(--muted); margin-top: 14px; line-height: 1.5; }

  main { display: flex; flex-direction: column; min-height: 0; }
  #log { flex: 1; overflow-y: auto; padding: 26px 22px 10px; }
  .inner { max-width: 760px; margin: 0 auto; }

  .msg { margin-bottom: 20px; display: flex; gap: 11px; }
  .msg .who { width: 27px; height: 27px; border-radius: 8px; flex-shrink: 0;
              display: flex; align-items: center; justify-content: center; font-size: 0.85rem; }
  .msg.user .who { background: var(--card-border); }
  .msg.bot .who { background: linear-gradient(135deg, var(--accent), var(--accent-dark)); }
  .bubble { flex: 1; min-width: 0; }
  .bubble .body { white-space: pre-wrap; }
  .msg.user .body { color: var(--text); }
  .msg.bot .body { color: var(--text); }

  .call { border: 1px solid var(--card-border); background: rgba(13,10,8,0.75);
          border-radius: 12px; margin: 9px 0; overflow: hidden; }
  .call .head { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
                background: var(--accent-soft); border-bottom: 1px solid var(--card-border); }
  .call .head code { font-family: Consolas, monospace; font-size: 0.78rem; color: var(--accent); font-weight: 700; }
  .call .head .lbl { font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.09em;
                     color: var(--muted); font-weight: 800; }
  .call pre { margin: 0; padding: 10px 12px; font-family: Consolas, monospace;
              font-size: 0.78rem; color: var(--muted); white-space: pre-wrap;
              word-break: break-word; overflow-x: auto; }
  .call pre + pre { border-top: 1px dashed var(--card-border); color: var(--text); }

  .thinking { color: var(--muted); font-size: 0.87rem; display: flex; align-items: center; gap: 8px; }
  .thinking i { width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
                display: inline-block; animation: pulse 1.1s ease-in-out infinite; }
  .thinking i:nth-child(2) { animation-delay: 0.16s; }
  .thinking i:nth-child(3) { animation-delay: 0.32s; }
  @keyframes pulse { 0%,100% { opacity: 0.25; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1); } }

  .empty { color: var(--muted); text-align: center; padding: 40px 0 26px; }
  .empty h3 { color: var(--text); margin: 0 0 6px; font-size: 1.15rem; }
  .starters { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 18px; }
  .starters button { border: 1px solid var(--card-border); background: rgba(25,19,16,0.6);
                     color: var(--muted); border-radius: 999px; padding: 7px 14px;
                     font-size: 0.8rem; cursor: pointer; font-family: inherit;
                     transition: border-color 0.15s ease, color 0.15s ease; }
  .starters button:hover { border-color: var(--accent); color: var(--text); }

  .composer { border-top: 1px solid var(--card-border); padding: 14px 22px 20px;
              background: rgba(25,19,16,0.5); backdrop-filter: blur(12px); }
  .composer .inner { display: flex; gap: 10px; align-items: flex-end; }
  textarea { flex: 1; resize: none; background: var(--ink); color: var(--text);
             border: 1px solid var(--card-border); border-radius: 12px; padding: 12px 14px;
             font-family: inherit; font-size: 0.94rem; line-height: 1.5; max-height: 180px;
             box-shadow: inset 0 2px 6px rgba(0,0,0,0.4); transition: border-color 0.15s ease, box-shadow 0.15s ease; }
  textarea:focus { outline: none; border-color: var(--accent); box-shadow: inset 0 2px 6px rgba(0,0,0,0.4), 0 0 0 3px var(--accent-soft); }
  .send { position: relative; overflow: hidden; background: var(--accent); color: #fff;
          border: none; border-radius: 12px; padding: 12px 22px; font-weight: 700;
          font-size: 0.92rem; font-family: inherit; cursor: pointer;
          box-shadow: 0 8px 20px -8px rgba(245,158,11,0.55); transition: background 0.15s ease; }
  .send:hover:not(:disabled) { background: var(--accent-dark); }
  .send:disabled { opacity: 0.5; cursor: default; }
  .hint { max-width: 760px; margin: 8px auto 0; font-size: 0.72rem; color: var(--muted); }
</style>
</head>
<body>

<header>
  <div class="brand"><span class="bot">&#129302;</span> Raybot Agent</div>
  <span class="chip">{{ model }}</span>
  <span class="chip">
    <span class="dot {{ 'up' if ollama_up else 'down' }}"></span>{{ 'ollama up' if ollama_up else 'ollama down' }}
  </span>
  <div class="spacer"></div>
  <span class="chip">{{ tools|length }} tools</span>
</header>

<div class="shell">
  <aside>
    <h2>Tools it can call</h2>
    {% for t in tools %}
    <div class="tool"><code>{{ t.name }}</code><p>{{ t.description }}</p></div>
    {% endfor %}
    {% if not curriculum %}
    <p class="note">Curriculum tools are hidden because curriculum.db wasn't found &mdash;
      only code execution is available.</p>
    {% endif %}
    <p class="note">Every tool call the model makes is shown inline in the conversation,
      with the exact arguments and the raw result it got back.</p>
  </aside>

  <main>
    <div id="log">
      <div class="inner" id="inner">
        <div class="empty" id="empty">
          <h3>Ask it something</h3>
          <div>It runs Python and reads your real progress rather than guessing.</div>
          <div class="starters">
            <button data-q="What does sum(range(11)) evaluate to?">Evaluate some Python</button>
            <button data-q="How am I doing so far?">Check my progress</button>
            <button data-q="Which categories do I still have problems left in?">Categories left</button>
            <button data-q="Explain how a list comprehension works, with a runnable example.">Explain a concept</button>
          </div>
        </div>
      </div>
    </div>

    <div class="composer">
      <div class="inner">
        <textarea id="input" rows="1" placeholder="Ask Raybot something…"></textarea>
        <button class="send" id="send">Send</button>
      </div>
      <div class="hint">Enter to send, Shift+Enter for a new line. Local model &mdash; first reply may take a few seconds.</div>
    </div>
  </main>
</div>

<script>
const log = document.getElementById('log');
const inner = document.getElementById('inner');
const input = document.getElementById('input');
const send = document.getElementById('send');
let history = [];
let busy = false;

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function scroll() { log.scrollTop = log.scrollHeight; }

function addMessage(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  div.innerHTML = '<div class="who">' + (role === 'user' ? '&#128100;' : '&#129302;') +
    '</div><div class="bubble"><div class="body">' + esc(text) + '</div></div>';
  inner.appendChild(div);
  scroll();
  return div.querySelector('.bubble');
}

function addToolCalls(bubble, trace) {
  const body = bubble.querySelector('.body');
  trace.forEach(t => {
    const el = document.createElement('div');
    el.className = 'call';
    const args = Object.keys(t.args || {}).length ? JSON.stringify(t.args, null, 2) : '(no arguments)';
    el.innerHTML =
      '<div class="head"><span class="lbl">tool call</span><code>' + esc(t.tool) + '</code></div>' +
      '<pre>' + esc(args) + '</pre>' +
      '<pre>' + esc(t.result) + '</pre>';
    bubble.insertBefore(el, body);
  });
  scroll();
}

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  send.disabled = true;
  const empty = document.getElementById('empty');
  if (empty) empty.remove();

  addMessage('user', question);
  const bubble = addMessage('bot', '');
  const body = bubble.querySelector('.body');
  body.innerHTML = '<div class="thinking"><i></i><i></i><i></i> thinking…</div>';

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message: question, history })
    });
    const data = await res.json();
    if (data.error) {
      body.textContent = data.error;
    } else {
      body.textContent = data.reply;
      if (data.trace && data.trace.length) addToolCalls(bubble, data.trace);
      history = data.history;
    }
  } catch (err) {
    body.textContent = 'Request failed: ' + err.message;
  } finally {
    busy = false;
    send.disabled = false;
    input.focus();
    scroll();
  }
}

send.addEventListener('click', () => { const q = input.value; input.value = ''; input.style.height = 'auto'; ask(q); });
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send.click(); }
});
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 180) + 'px';
});
document.querySelectorAll('.starters button').forEach(b => {
  b.addEventListener('click', () => ask(b.dataset.q));
});
input.focus();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    specs = [
        {
            "name": spec["function"]["name"],
            "description": spec["function"]["description"],
        }
        for spec in toolkit.SPECS
    ]
    return render_template_string(
        PAGE,
        model=agent.MODEL,
        tools=specs,
        ollama_up=ollama_is_up(),
        curriculum=toolkit.CURRICULUM_AVAILABLE,
    )


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    # `(data.get("message") or "").strip()` looked like it handled a missing
    # message, but only guards *falsy* values -- a body like
    # {"message": 123} is truthy, skips the "" fallback, and .strip() on an
    # int raised AttributeError outside this route's own try/except (which
    # only wraps agent.run_turn), producing a raw unhandled 500 instead of
    # the clean JSON error every other bad-input path here returns.
    raw_message = data.get("message")
    message = raw_message.strip() if isinstance(raw_message, str) else ""
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "Nothing to answer — send a question."}), 400

    # Trust the shape of what came back, but bound it.
    history = [
        m for m in history
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ][-MAX_HISTORY_MESSAGES:]

    try:
        reply, trace, new_history = agent.run_turn(history, message)
    except agent.OllamaUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:  # surfaced in the UI rather than a blank 500
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    return jsonify({"reply": reply, "trace": trace, "history": new_history})
