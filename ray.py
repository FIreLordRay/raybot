"""Ray — the agent is the product; the curriculum is what it works on.

This combines the two earlier pieces into one app on one port:

  * The agent (raybot_agent) is the hero. The front page is a conversation with
    it, and it can run Python and read your real progress while answering.
  * The dashboard contributes only what supports that: live stats beside the
    chat, a problem browser, and a solve-and-grade view. Every curriculum page
    carries an "Ask Ray" box wired to the same agent, with the problem as context.

    python ray.py     ->  http://localhost:5000

Needs, for full functionality:
  * Ollama running locally with a tool-capable model (see raybot_agent/README.md)
  * The python-practice folder + curriculum.db

Missing either degrades rather than crashes: without Ollama the chat reports it
and the curriculum still works; without the database the agent still runs code.
"""

import sys
import threading
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template_string, request, url_for

from raybot_agent import agent
from raybot_agent import tools as toolkit

# --- optional curriculum backend -------------------------------------------------
PRACTICE_DIR = Path(r"C:\Users\rayra\Documents\python-practice")

db = None
grading = None
badges = None
try:
    if PRACTICE_DIR.exists():
        sys.path.insert(0, str(PRACTICE_DIR))
        import badges  # noqa: E402
        import db  # noqa: E402
        import grading  # noqa: E402

        db.init_db()
except Exception:  # pragma: no cover - environment dependent
    db = grading = badges = None

CURRICULUM = db is not None
GRADE_TIMEOUT_SECONDS = 5

app = Flask(__name__)


def stats():
    """Everything the context rail shows. Safe to call with no database."""
    if not CURRICULUM:
        return None
    total = len(db.all_problems())
    solved = len(db.get_solved_ids())
    earned = badges.earned_badges() if badges else []
    return {
        "solved": solved,
        "total": total,
        "pct": round((solved / total) * 100, 1) if total else 0,
        "streak": db.get_streak(),
        "quizzes": len(db.get_quiz_history()),
        "categories": db.category_stats(),
        "badges": len(earned),
        "badge_names": [b["name"] for b in earned][:6],
    }


def grade(problem, code):
    """Run the shared grader with a wall-clock bound (it is synchronous/un-timed)."""
    box = {}

    def target():
        box["result"] = grading.exec_and_test(problem, code)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(GRADE_TIMEOUT_SECONDS)
    if thread.is_alive():
        return 0, len(problem["tests"]), f"Timed out after {GRADE_TIMEOUT_SECONDS}s."
    return box.get("result", (0, len(problem["tests"]), "Grader did not return."))


# ---------------------------------------------------------------------------------
STYLE = """
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
    color: var(--text); line-height: 1.6; -webkit-font-smoothing: antialiased;
    font-family: "Plus Jakarta Sans", -apple-system, "Segoe UI", Arial, sans-serif;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  ::selection { background: var(--accent); color: #1a1002; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  ::-webkit-scrollbar { width: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--card-border); border-radius: 999px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--accent-dark); }

  header {
    display: flex; align-items: center; gap: 11px; flex-wrap: wrap;
    padding: 13px 22px; border-bottom: 1px solid var(--card-border);
    background: rgba(25,19,16,0.6); backdrop-filter: blur(12px); flex-shrink: 0;
  }
  .brand { display: flex; align-items: center; gap: 9px; font-weight: 800;
           letter-spacing: -0.02em; font-size: 1.05rem; color: var(--text); }
  .brand:hover { text-decoration: none; }
  .brand .bot { font-size: 1.3rem; animation: float 2.4s ease-in-out infinite; }
  @keyframes float { 0%,100% { transform: translateY(0) rotate(0); }
                     25% { transform: translateY(-3px) rotate(-8deg); }
                     75% { transform: translateY(-2px) rotate(8deg); } }
  .chip { border: 1px solid var(--card-border); background: rgba(13,10,8,0.6); color: var(--muted);
          border-radius: 999px; padding: 4px 12px; font-size: 0.73rem; font-weight: 600;
          font-family: Consolas, monospace; white-space: nowrap; }
  .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .dot.up { background: var(--good); box-shadow: 0 0 7px var(--good); }
  .dot.down { background: var(--bad); box-shadow: 0 0 7px var(--bad); }
  header .spacer { flex: 1; }
  header nav a { color: var(--muted); font-weight: 600; font-size: 0.86rem; padding: 6px 12px;
                 border-radius: 999px; border: 1px solid transparent; }
  header nav a:hover, header nav a.on { color: var(--text); border-color: var(--card-border);
                                        background: rgba(13,10,8,0.5); text-decoration: none; }

  .btn { position: relative; overflow: hidden; display: inline-block; background: var(--accent);
         color: #fff; border: none; border-radius: 11px; padding: 11px 22px; font-weight: 700;
         font-size: 0.92rem; font-family: inherit; cursor: pointer; text-decoration: none;
         box-shadow: 0 8px 20px -8px rgba(245,158,11,0.55); transition: background 0.15s ease; }
  .btn:hover { background: var(--accent-dark); text-decoration: none; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .btn.ghost { background: rgba(25,19,16,0.75); border: 1px solid var(--card-border); box-shadow: none; color: var(--text); }
  .btn.ghost:hover { background: var(--accent-soft); }

  .card { position: relative; background: rgba(25,19,16,0.55); backdrop-filter: blur(14px);
          border: 1px solid var(--card-border); border-radius: 16px; padding: 18px; }
  .card::before { content: ""; position: absolute; top: 0; left: 16px; right: 16px; height: 1px;
                  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.16), transparent); }
  .kicker { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.1em;
            color: var(--accent); font-weight: 800; margin-bottom: 12px; }

  /* tool-call cards + chat, shared by the hero chat and the inline Ask Ray boxes */
  .call { border: 1px solid var(--card-border); background: rgba(13,10,8,0.75);
          border-radius: 11px; margin: 9px 0; overflow: hidden; }
  .call .head { display: flex; align-items: center; gap: 8px; padding: 7px 11px;
                background: var(--accent-soft); border-bottom: 1px solid var(--card-border); }
  .call .head code { font-family: Consolas, monospace; font-size: 0.76rem; color: var(--accent); font-weight: 700; }
  .call .head .lbl { font-size: 0.63rem; text-transform: uppercase; letter-spacing: 0.09em;
                     color: var(--muted); font-weight: 800; }
  .call pre { margin: 0; padding: 9px 11px; font-family: Consolas, monospace; font-size: 0.76rem;
              color: var(--muted); white-space: pre-wrap; word-break: break-word; }
  .call pre + pre { border-top: 1px dashed var(--card-border); color: var(--text); }
  .thinking { color: var(--muted); font-size: 0.86rem; display: flex; align-items: center; gap: 7px; }
  .thinking i { width: 6px; height: 6px; border-radius: 50%; background: var(--accent);
                display: inline-block; animation: pulse 1.1s ease-in-out infinite; }
  .thinking i:nth-child(2) { animation-delay: 0.16s; }
  .thinking i:nth-child(3) { animation-delay: 0.32s; }
  @keyframes pulse { 0%,100% { opacity: 0.25; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1); } }

  textarea, input[type=text] { width: 100%; background: var(--ink); color: var(--text);
    border: 1px solid var(--card-border); border-radius: 11px; padding: 11px 13px;
    font-family: inherit; font-size: 0.93rem; line-height: 1.5; resize: none;
    box-shadow: inset 0 2px 6px rgba(0,0,0,0.4); transition: border-color 0.15s ease, box-shadow 0.15s ease; }
  textarea:focus, input[type=text]:focus { outline: none; border-color: var(--accent);
    box-shadow: inset 0 2px 6px rgba(0,0,0,0.4), 0 0 0 3px var(--accent-soft); }
  .code { font-family: Consolas, "SF Mono", monospace; min-height: 190px; }
</style>
"""

HEADER = """
<header>
  <a class="brand" href="{{ url_for('home') }}"><span class="bot">&#129302;</span> Ray</a>
  <span class="chip">{{ model }}</span>
  <span class="chip"><span class="dot {{ 'up' if ollama_up else 'down' }}"></span>{{ 'ollama up' if ollama_up else 'ollama down' }}</span>
  <div class="spacer"></div>
  <nav>
    <a class="{{ 'on' if page == 'chat' else '' }}" href="{{ url_for('home') }}">Chat</a>
    {% if curriculum %}<a class="{{ 'on' if page == 'problems' else '' }}" href="{{ url_for('problems') }}">Problems</a>{% endif %}
  </nav>
</header>
"""

# Shared chat client. `mount(root, opts)` turns any container into an Ask Ray box;
# the hero chat is the same widget with a transcript and starters.
CHAT_JS = """
<script>
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function makeChat(opts){
  const log = opts.log, input = opts.input, send = opts.send;
  let history = [], busy = false;

  function bubble(role, text){
    const d = document.createElement('div');
    d.className = 'msg ' + role;
    d.innerHTML = '<div class="who">' + (role==='user'?'&#128100;':'&#129302;') +
      '</div><div class="bubble"><div class="body">' + esc(text) + '</div></div>';
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d.querySelector('.bubble');
  }

  async function ask(q){
    if (busy || !q.trim()) return;
    busy = true; if (send) send.disabled = true;
    const empty = log.querySelector('.empty'); if (empty) empty.remove();
    bubble('user', q);
    const b = bubble('bot', ''), body = b.querySelector('.body');
    body.innerHTML = '<div class="thinking"><i></i><i></i><i></i> thinking…</div>';
    try {
      const res = await fetch('/api/chat', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({message:q, history:history, problem_id: opts.problemId || null})
      });
      const data = await res.json();
      if (data.error) { body.textContent = data.error; }
      else {
        body.textContent = data.reply;
        (data.trace||[]).forEach(t => {
          const el = document.createElement('div'); el.className = 'call';
          const args = Object.keys(t.args||{}).length ? JSON.stringify(t.args, null, 2) : '(no arguments)';
          el.innerHTML = '<div class="head"><span class="lbl">tool call</span><code>' + esc(t.tool) +
            '</code></div><pre>' + esc(args) + '</pre><pre>' + esc(t.result) + '</pre>';
          b.insertBefore(el, body);
        });
        history = data.history;
        if (opts.onReply) opts.onReply(data);
      }
    } catch(e){ body.textContent = 'Request failed: ' + e.message; }
    finally { busy = false; if (send) send.disabled = false; log.scrollTop = log.scrollHeight; }
  }

  if (send) send.addEventListener('click', () => { const q = input.value; input.value=''; input.style.height='auto'; ask(q); });
  if (input) {
    input.addEventListener('keydown', e => { if (e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send.click(); } });
    input.addEventListener('input', () => { input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,170)+'px'; });
  }
  return { ask };
}
</script>
"""

CHAT_CSS = """
<style>
  .msg { margin-bottom: 18px; display: flex; gap: 10px; }
  .msg .who { width: 26px; height: 26px; border-radius: 8px; flex-shrink: 0;
              display: flex; align-items: center; justify-content: center; font-size: 0.82rem; }
  .msg.user .who { background: var(--card-border); }
  .msg.bot .who { background: linear-gradient(135deg, var(--accent), var(--accent-dark)); }
  .bubble { flex: 1; min-width: 0; }
  .bubble .body { white-space: pre-wrap; }
</style>
"""

HOME = STYLE + CHAT_CSS + HEADER + """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ray</title></head>
<body>
<style>
  .shell { flex: 1; display: grid; grid-template-columns: 1fr 300px; min-height: 0; }
  @media (max-width: 920px) { .shell { grid-template-columns: 1fr; } .rail { display: none; } }
  main { display: flex; flex-direction: column; min-height: 0; }
  #log { flex: 1; overflow-y: auto; padding: 26px 22px 8px; }
  .inner { max-width: 720px; margin: 0 auto; }
  .composer { border-top: 1px solid var(--card-border); padding: 14px 22px 18px;
              background: rgba(25,19,16,0.5); backdrop-filter: blur(12px); }
  .composer .inner { display: flex; gap: 10px; align-items: flex-end; }
  .hint { max-width: 720px; margin: 8px auto 0; font-size: 0.71rem; color: var(--muted); }
  .empty { text-align: center; padding: 36px 0 22px; color: var(--muted); }
  .empty h1 { color: var(--text); font-size: 1.5rem; margin: 0 0 6px; letter-spacing: -0.02em; }
  .starters { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 16px; }
  .starters button { border: 1px solid var(--card-border); background: rgba(25,19,16,0.6);
    color: var(--muted); border-radius: 999px; padding: 7px 14px; font-size: 0.79rem;
    cursor: pointer; font-family: inherit; transition: border-color 0.15s ease, color 0.15s ease; }
  .starters button:hover { border-color: var(--accent); color: var(--text); }

  .rail { border-left: 1px solid var(--card-border); padding: 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
  .big { font-size: 1.9rem; font-weight: 800; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
  .lbl { color: var(--muted); font-size: 0.78rem; }
  .bar { background: var(--ink); border-radius: 999px; height: 7px; overflow: hidden; margin-top: 11px; }
  .fill { background: linear-gradient(90deg, var(--accent), var(--accent-dark)); height: 100%; border-radius: 999px; transition: width 0.4s ease; }
  .row { display: flex; justify-content: space-between; gap: 10px; font-size: 0.82rem; padding: 5px 0; }
  .row span:last-child { color: var(--muted); font-variant-numeric: tabular-nums; }
  .badges { display: flex; flex-wrap: wrap; gap: 6px; }
  .b { font-size: 0.7rem; border: 1px solid #6b5620; background: linear-gradient(160deg, #2e2410, #201d2b);
       border-radius: 999px; padding: 4px 10px; }
</style>

<div class="shell">
  <main>
    <div id="log">
      <div class="inner" id="inner">
        <div class="empty">
          <h1>Ask Ray</h1>
          <div>It runs Python and reads your real progress instead of guessing.</div>
          <div class="starters">
            <button data-q="What does sum(range(11)) evaluate to?">Evaluate some Python</button>
            <button data-q="How am I doing so far?">Check my progress</button>
            <button data-q="Which categories should I work on next?">What's next</button>
            <button data-q="Explain list comprehensions with a runnable example.">Explain a concept</button>
          </div>
        </div>
      </div>
    </div>
    <div class="composer">
      <div class="inner">
        <textarea id="input" rows="1" placeholder="Ask Ray something…"></textarea>
        <button class="btn" id="send">Send</button>
      </div>
      <div class="hint">Enter to send, Shift+Enter for a newline. Local model &mdash; the first reply takes a moment.</div>
    </div>
  </main>

  <aside class="rail">
    {% if s %}
    <div class="card">
      <div class="kicker">Progress</div>
      <div class="big">{{ s.solved }}<span style="color:var(--muted);font-size:1rem;">/{{ s.total }}</span></div>
      <div class="lbl">problems solved &middot; {{ s.pct }}%</div>
      <div class="bar"><div class="fill" id="fill" style="width: {{ s.pct }}%"></div></div>
    </div>
    <div class="card">
      <div class="kicker">Streak</div>
      <div class="big">&#128293; <span id="streak">{{ s.streak }}</span></div>
      <div class="lbl">day streak &middot; {{ s.quizzes }} quizzes taken</div>
    </div>
    <div class="card">
      <div class="kicker">Categories</div>
      {% for c in s.categories[:7] %}
      <div class="row"><span>{{ c.name }}</span><span>{{ c.solved }}/{{ c.total }}</span></div>
      {% endfor %}
      <div style="margin-top:10px"><a href="{{ url_for('problems') }}">Browse all problems &rarr;</a></div>
    </div>
    {% if s.badge_names %}
    <div class="card">
      <div class="kicker">Badges ({{ s.badges }})</div>
      <div class="badges">{% for b in s.badge_names %}<span class="b">{{ b }}</span>{% endfor %}</div>
    </div>
    {% endif %}
    {% else %}
    <div class="card">
      <div class="kicker">Curriculum</div>
      <div class="lbl">curriculum.db wasn't found, so progress tools are off. Ray can still run Python.</div>
    </div>
    {% endif %}
  </aside>
</div>
""" + CHAT_JS + """
<script>
  const chat = makeChat({
    log: document.getElementById('inner'),
    input: document.getElementById('input'),
    send: document.getElementById('send'),
  });
  document.querySelectorAll('.starters button').forEach(b =>
    b.addEventListener('click', () => chat.ask(b.dataset.q)));
  document.getElementById('input').focus();
</script>
</body></html>
"""

PROBLEMS = STYLE + HEADER + """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Problems &mdash; Ray</title></head>
<body>
<style>
  .wrap { max-width: 880px; margin: 0 auto; padding: 28px 22px 60px; width: 100%; overflow-y: auto; }
  h1 { font-size: 1.6rem; font-weight: 800; letter-spacing: -0.02em; margin: 0 0 4px; }
  .sub { color: var(--muted); margin-bottom: 22px; font-size: 0.9rem; }
  .p { display: flex; align-items: center; gap: 13px; padding: 12px; margin: 0 -12px;
       border-radius: 11px; border-bottom: 1px solid var(--card-border); transition: background-color 0.15s ease; }
  .p:hover { background: rgba(245,158,11,0.06); }
  .p:last-child { border-bottom: none; }
  .p .t { flex: 1; min-width: 0; }
  .p .t b { font-weight: 700; font-size: 0.93rem; }
  .p .t div { color: var(--muted); font-size: 0.77rem; }
  .tick { width: 24px; text-align: center; }
</style>
<div class="wrap">
  <h1>Problems</h1>
  <div class="sub">{{ problems|length }} in the curriculum &middot; {{ solved|length }} solved</div>
  <div class="card">
    {% for p in problems %}
    <div class="p">
      <div class="tick">{{ '&#9989;'|safe if p.id in solved else '&#9675;'|safe }}</div>
      <div class="t">
        <b>{{ p.title }}</b>
        <div>{{ p.category }} &middot; difficulty {{ p.difficulty }}</div>
      </div>
      <a class="btn ghost" href="{{ url_for('problem', problem_id=p.id) }}">Open</a>
    </div>
    {% endfor %}
  </div>
</div>
</body></html>
"""

PROBLEM = STYLE + CHAT_CSS + HEADER + """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ p.title }} &mdash; Ray</title></head>
<body>
<style>
  .wrap { max-width: 860px; margin: 0 auto; padding: 26px 22px 60px; width: 100%; overflow-y: auto; }
  h1 { font-size: 1.5rem; font-weight: 800; letter-spacing: -0.02em; margin: 0 0 4px; }
  .sub { color: var(--muted); margin-bottom: 20px; font-size: 0.87rem; }
  .lesson { background: var(--accent-soft); border-left: 3px solid var(--accent);
            border-radius: 10px; padding: 13px 15px; margin-bottom: 16px; font-size: 0.89rem; }
  .prompt { margin: 14px 0 16px; }
  .fb { padding: 11px 15px; border-radius: 10px; margin-bottom: 16px; font-size: 0.89rem;
        border: 1px solid transparent; border-left-width: 3px; white-space: pre-line; }
  .fb.ok { background: #12291f; color: #4ade80; border-color: #1f4a35; border-left-color: #4ade80; }
  .fb.bad { background: #2c1418; color: #f87171; border-color: #4a2028; border-left-color: #f87171; }
  .ask { margin-top: 18px; }
  #asklog:empty { display: none; }
  #asklog { margin-bottom: 12px; }
  .askrow { display: flex; gap: 10px; align-items: flex-end; }
</style>
<div class="wrap">
  <h1>{{ p.title }} {{ '&#9989;'|safe if done else '' }}</h1>
  <div class="sub"><a href="{{ url_for('problems') }}">&larr; all problems</a> &middot; {{ p.category }} &middot; difficulty {{ p.difficulty }}</div>

  {% if feedback %}<div class="fb {{ 'ok' if ok else 'bad' }}">{{ feedback }}</div>{% endif %}

  <div class="card">
    {% if p.lesson %}<div class="lesson"><div class="kicker">The idea</div>{{ p.lesson }}</div>{% endif %}
    <div class="prompt">{{ p.prompt }}</div>
    <form method="post">
      <textarea class="code" name="code" spellcheck="false">{{ code }}</textarea>
      <div style="margin-top: 12px;"><button class="btn" type="submit">Submit</button></div>
    </form>
  </div>

  <div class="card ask">
    <div class="kicker">Ask Ray about this problem</div>
    <div id="asklog"></div>
    <div class="askrow">
      <textarea id="askinput" rows="1" placeholder="Stuck? Ask about the concept…"></textarea>
      <button class="btn ghost" id="asksend">Ask</button>
    </div>
  </div>
</div>
""" + CHAT_JS + """
<script>
  makeChat({
    log: document.getElementById('asklog'),
    input: document.getElementById('askinput'),
    send: document.getElementById('asksend'),
    problemId: {{ p.id|tojson }},
  });
</script>
</body></html>
"""


def render(template, **context):
    context.setdefault("model", agent.MODEL)
    context.setdefault("ollama_up", _ollama_up())
    context.setdefault("curriculum", CURRICULUM)
    return render_template_string(template, **context)


def _ollama_up():
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


# --- routes ----------------------------------------------------------------------
@app.route("/")
def home():
    return render(HOME, page="chat", s=stats())


@app.route("/problems")
def problems():
    if not CURRICULUM:
        return redirect(url_for("home"))
    return render(PROBLEMS, page="problems",
                  problems=db.all_problems(), solved=db.get_solved_ids())


@app.route("/problem/<problem_id>", methods=["GET", "POST"])
def problem(problem_id):
    if not CURRICULUM:
        return redirect(url_for("home"))
    p = db.problem_by_id(problem_id)
    if p is None:
        abort(404)

    code = p.get("starter") or ""
    feedback = None
    ok = False

    if request.method == "POST":
        code = request.form.get("code", "")
        passed, total, error = grade(p, grading.clean_code(code))
        if error:
            feedback = error
        elif passed == total:
            ok = True
            db.mark_solved(p["id"], user_name="you", mode="ray")
            feedback = f"All {total} tests passed. Nice one."
        else:
            feedback = f"{passed} of {total} tests passed — not there yet."

    return render(PROBLEM, page="problems", p=p, code=code,
                  feedback=feedback, ok=ok, done=p["id"] in db.get_solved_ids())


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    problem_id = data.get("problem_id")

    if not message:
        return jsonify({"error": "Nothing to answer — send a question."}), 400

    history = [
        m for m in history
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ][-20:]

    # On a problem page, hand the agent the problem as context so "why is this
    # failing?" means something without the learner restating it. The "no need to
    # look it up" line matters: without it the model burns a round-trip calling
    # find_problem for the exercise it was just handed.
    if problem_id and CURRICULUM:
        p = db.problem_by_id(problem_id)
        if p:
            message = (
                f"[Context — the learner is working on this exercise right now. "
                f"You already have it, so do not call find_problem for it.\n"
                f"Title: {p['title']}\nCategory: {p['category']}\n"
                f"Prompt: {p['prompt']}]\n\n{message}"
            )

    try:
        reply, trace, new_history = agent.run_turn(history, message)
    except agent.OllamaUnavailable as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    return jsonify({"reply": reply, "trace": trace, "history": new_history})


@app.route("/api/stats")
def api_stats():
    return jsonify(stats() or {})


if __name__ == "__main__":
    app.run(port=5000, debug=False)
