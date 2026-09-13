"""Raybot showcase: a single-page site about the AI itself and what it can do.

Standalone from dashboard.py on purpose — this one has no database, no session,
and no curriculum imports. It is pure presentation, so it can be run (or shown
to someone) without Ollama, Discord, or curriculum.db being set up at all.

Everything it claims is sourced from the real code:
    discord_raybot.py   command surface
    tutor.py            ask_raybot / explain_failure / generate_lesson
    generator.py        generate_problem + self-verification loop
    grading.py          exec_and_test, denylist, timeout

Run it alongside the dashboard (which uses 5001):

    python raybot_showcase.py

Then open http://localhost:5002
"""

from flask import Flask, render_template_string

app = Flask(__name__)

HERO_VIDEO = "https://videos.pexels.com/video-files/12257306/12257306-uhd_2560_1440_60fps.mp4"

CAPABILITIES = [
    {
        "icon": "&#128172;",
        "title": "Answers questions",
        "body": "Ask it anything mid-exercise. It explains the underlying concept so you can "
                "write the solution yourself — it won't hand you finished code unless you ask.",
        "tag": "tutor.ask_raybot",
    },
    {
        "icon": "&#128269;",
        "title": "Explains what broke",
        "body": "Fail a test and it reads your actual submission, then tells you why it failed "
                "in plain language instead of dumping a traceback at you.",
        "tag": "tutor.explain_failure",
    },
    {
        "icon": "&#128218;",
        "title": "Writes the lesson",
        "body": "Every exercise gets a short 'The idea' primer generated for it, so a new "
                "concept is introduced before you're asked to use it.",
        "tag": "tutor.generate_lesson",
    },
    {
        "icon": "&#129513;",
        "title": "Grows its own curriculum",
        "body": "When a category runs out of problems, it writes a new one — title, prompt, "
                "tests and a reference solution — rather than repeating itself.",
        "tag": "generator.generate_problem",
    },
    {
        "icon": "&#9989;",
        "title": "Grades real code",
        "body": "Submissions are executed against the problem's tests. A denylist blocks "
                "file, network and process access, and a timeout bounds slow code.",
        "tag": "grading.exec_and_test",
    },
    {
        "icon": "&#128293;",
        "title": "Tracks the long game",
        "body": "Streaks, per-category progress, quiz history, a leaderboard and achievement "
                "badges — all sharing one database across Discord, CLI and dashboard.",
        "tag": "db + badges",
    },
]

PIPELINE = [
    ("Writes a problem", "The model returns structured JSON: a title, a prompt, a set of tests, "
                         "and its own reference solution."),
    ("Grades its own work", "That reference solution is run against those generated tests, "
                            "through the same grader real submissions go through."),
    ("Clears the denylist", "The generated solution has to pass the same safety checks a "
                            "student submission does."),
    ("Ships or retries", "Anything that fails is thrown away and regenerated, up to three "
                         "attempts. Broken problems never reach a learner."),
]

COMMANDS = [
    ("!quiz", "Posts a race-mode problem in the channel. First working solution wins and it's "
              "marked solved for everyone."),
    ("!firelordray", "DMs you a personal 5-question quiz, optionally filtered by difficulty "
                     "and category, and tracks your score over time."),
    ("!progress", "Shared curriculum progress and current streak."),
    ("!categories", "Every category with its per-category completion."),
    ("!leaderboard", "Top solvers, ranked by distinct problems solved."),
    ("!badges", "Your earned and still-locked achievement badges."),
    ("!setquizchannel", "Marks a channel as the one that gets the daily auto-posted question."),
    ("!raybothelp", "Lists the commands."),
]

PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Raybot &mdash; the AI</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

  :root {
    --bg: #100c0a;
    --card: #191310;
    --card-border: #332a22;
    --text: #f7f2ec;
    --muted: #a09488;
    --accent: #f59e0b;
    --accent-dark: #d97706;
    --accent-soft: #2e2410;
    --ink: #0d0a08;
  }

  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; background: var(--bg); }
  body {
    margin: 0;
    isolation: isolate;
    background-color: var(--bg);
    background-image:
      radial-gradient(circle at center, rgba(255,255,255,0.05) 1px, transparent 1px),
      radial-gradient(ellipse 70% 62% at 4% 100%, rgba(245,158,11,0.34) 0%, transparent 82%),
      radial-gradient(ellipse 66% 58% at 100% 12%, rgba(244,63,94,0.24) 0%, transparent 82%),
      radial-gradient(circle at 50% 120%, #1a1210 0%, #100c0a 48%, #080605 100%);
    background-size: 4px 4px, auto, auto, auto;
    background-repeat: repeat, no-repeat, no-repeat, no-repeat;
    background-attachment: fixed;
    color: var(--text);
    font-family: "Plus Jakarta Sans", -apple-system, "Segoe UI", Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
    line-height: 1.6;
  }

  ::selection { background: var(--accent); color: #1a1002; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
  ::-webkit-scrollbar { width: 11px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--card-border); border-radius: 999px; border: 2px solid var(--bg); }
  ::-webkit-scrollbar-thumb:hover { background: var(--accent-dark); }

  #smooth-cursor { position: fixed; top: 0; left: 0; width: 22px; height: 22px; pointer-events: none; z-index: 9999; will-change: transform; }
  @media (pointer: fine) { * { cursor: none !important; } }

  .wrap { max-width: 1000px; margin: 0 auto; padding: 0 24px; }

  /* ---- hero ---- */
  .hero { position: relative; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 80px 24px; }
  .hero-video { position: fixed; inset: 0; z-index: -1; width: 100%; height: 100%; object-fit: cover; }
  .hero-overlay { position: fixed; inset: 0; z-index: -1; background: linear-gradient(180deg, rgba(16,12,10,0.5) 0%, rgba(16,12,10,0.8) 60%, rgba(16,12,10,0.97) 100%); }
  .mark { font-size: 4.5rem; line-height: 1; animation: float 2.4s ease-in-out infinite; }
  @keyframes float {
    0%, 100% { transform: translateY(0) rotate(0deg); }
    25% { transform: translateY(-4px) rotate(-8deg); }
    75% { transform: translateY(-2px) rotate(8deg); }
  }
  h1 { font-size: clamp(2.6rem, 7vw, 4.2rem); font-weight: 800; letter-spacing: -0.035em; margin: 10px 0 14px; text-shadow: 0 4px 28px rgba(0,0,0,0.55); }
  .lede { color: var(--muted); font-size: 1.1rem; max-width: 540px; margin: 0 auto 18px; }
  .pills { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-bottom: 34px; }
  .pill { border: 1px solid var(--card-border); background: rgba(25,19,16,0.6); backdrop-filter: blur(10px); color: var(--muted); border-radius: 999px; padding: 5px 14px; font-size: 0.78rem; font-weight: 600; }
  .pill b { color: var(--accent); font-weight: 700; }
  .cta-row { display: flex; gap: 14px; flex-wrap: wrap; justify-content: center; }

  /* ---- buttons (Uiverse.io by Spacious74, recolored) ---- */
  .glow { position: relative; z-index: 0; display: inline-block; padding: 3px; background: linear-gradient(90deg, #f59e0b, #f43f5e); border-radius: 13px; }
  .glow::before { content: ""; position: absolute; inset: 0; margin: auto; border-radius: 13px; z-index: -10; filter: blur(0); transition: filter 0.4s ease; }
  .glow:hover::before { background: linear-gradient(90deg, #f59e0b, #f43f5e); filter: blur(1.2em); }
  .glow:active::before { filter: blur(0.2em); }
  .btn { position: relative; display: block; overflow: hidden; background: var(--accent); color: #fff; font-weight: 700; text-decoration: none; padding: 12px 26px; border-radius: 10px; font-size: 0.95rem; box-shadow: 0 8px 20px -8px rgba(245,158,11,0.55); transition: background 0.15s ease, transform 0.15s ease; }
  .btn:hover { background: var(--accent-dark); }
  .btn:active { transform: translateY(1px); }
  .btn.ghost { background: rgba(25,19,16,0.75); border: 1px solid var(--card-border); box-shadow: none; }
  .btn.ghost:hover { background: var(--accent-soft); }
  .btn::after { content: ""; position: absolute; top: 0; left: -75%; width: 50%; height: 100%; background: linear-gradient(115deg, transparent, rgba(255,255,255,0.3), transparent); transform: skewX(-20deg); transition: left 0.5s ease; }
  .btn:hover::after { left: 125%; }

  /* ---- sections ---- */
  section { padding: 84px 0; }
  .kicker { color: var(--accent); text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.72rem; font-weight: 800; margin-bottom: 10px; }
  h2 { font-size: clamp(1.6rem, 3.4vw, 2.1rem); font-weight: 800; letter-spacing: -0.02em; margin: 0 0 10px; }
  .sub { color: var(--muted); max-width: 620px; margin: 0 0 34px; }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(272px, 1fr)); gap: 16px; }
  .card { position: relative; background: rgba(25,19,16,0.55); backdrop-filter: blur(14px); border: 1px solid var(--card-border); border-radius: 18px; padding: 24px; transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease; }
  .card::before { content: ""; position: absolute; top: 0; left: 16px; right: 16px; height: 1px; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent); }
  .card:hover { transform: translateY(-3px); border-color: rgba(245,158,11,0.35); box-shadow: 0 22px 38px -20px rgba(0,0,0,0.6); }
  .card .ico { font-size: 1.8rem; filter: drop-shadow(0 6px 10px rgba(0,0,0,0.5)); }
  .card h3 { font-size: 1.02rem; font-weight: 700; margin: 12px 0 8px; }
  .card p { color: var(--muted); font-size: 0.9rem; margin: 0 0 14px; }
  .tag { display: inline-block; font-family: Consolas, "SF Mono", monospace; font-size: 0.72rem; color: var(--accent); background: var(--accent-soft); border: 1px solid #6b4f12; border-radius: 999px; padding: 3px 10px; }

  /* ---- pipeline ---- */
  /* Explicit counts, not auto-fit: 4 sequential steps must never orphan one on its own row. */
  .steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; counter-reset: step; }
  @media (max-width: 900px) { .steps { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 560px) { .steps { grid-template-columns: 1fr; } }
  .step { position: relative; background: rgba(25,19,16,0.55); backdrop-filter: blur(14px); border: 1px solid var(--card-border); border-radius: 16px; padding: 22px; }
  .step::before { counter-increment: step; content: counter(step); display: flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 9px; background: linear-gradient(135deg, #f59e0b, #d97706); color: #1a1002; font-weight: 800; font-size: 0.85rem; margin-bottom: 12px; box-shadow: 0 6px 14px -6px rgba(245,158,11,0.7); }
  .step h4 { margin: 0 0 6px; font-size: 0.96rem; font-weight: 700; }
  .step p { margin: 0; color: var(--muted); font-size: 0.87rem; }

  /* ---- commands ---- */
  .cmds { background: rgba(25,19,16,0.55); backdrop-filter: blur(14px); border: 1px solid var(--card-border); border-radius: 18px; overflow: hidden; }
  .cmd { display: grid; grid-template-columns: 190px 1fr; gap: 18px; padding: 15px 22px; border-bottom: 1px solid var(--card-border); transition: background-color 0.15s ease; }
  .cmd:last-child { border-bottom: none; }
  .cmd:hover { background: rgba(245,158,11,0.06); }
  .cmd code { font-family: Consolas, "SF Mono", monospace; font-size: 0.85rem; color: var(--accent); font-weight: 700; }
  .cmd span { color: var(--muted); font-size: 0.88rem; }
  @media (max-width: 620px) { .cmd { grid-template-columns: 1fr; gap: 4px; } }

  /* ---- stack / footer ---- */
  .stack { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
  .stat { background: rgba(25,19,16,0.55); backdrop-filter: blur(14px); border: 1px solid var(--card-border); border-radius: 16px; padding: 22px; }
  .stat .v { font-family: Consolas, "SF Mono", monospace; font-size: 1.05rem; font-weight: 700; color: var(--accent); }
  .stat .k { color: var(--muted); font-size: 0.8rem; margin-top: 6px; }
  footer { border-top: 1px solid var(--card-border); padding: 30px 0 60px; color: var(--muted); font-size: 0.84rem; text-align: center; }
</style>
</head>
<body>

<video class="hero-video" autoplay muted loop playsinline>
  <source src="{{ hero_video }}" type="video/mp4">
</video>
<div class="hero-overlay"></div>

<header class="hero">
  <div class="mark">&#129302;</div>
  <h1 class="hyper">Raybot</h1>
  <p class="lede">A Python tutor that teaches, grades your code, explains what broke, and writes
    new exercises when it runs out &mdash; running entirely on your own machine.</p>
  <div class="pills">
    <span class="pill">Runs <b>100% locally</b></span>
    <span class="pill">Powered by <b>Ollama</b></span>
    <span class="pill">No cloud API</span>
  </div>
  <div class="cta-row">
    <div class="glow"><a class="btn" href="#capabilities">See what it does</a></div>
    <a class="btn ghost" href="http://localhost:5001/">Open the dashboard</a>
  </div>
</header>

<main>
  <section id="capabilities">
    <div class="wrap">
      <div class="kicker">Capabilities</div>
      <h2>Six things it actually does</h2>
      <p class="sub">Not a chat window bolted onto a course. Each of these is a distinct job it
        performs against your real submissions and your real progress.</p>
      <div class="grid">
        {% for c in capabilities %}
        <article class="card">
          <div class="ico">{{ c.icon|safe }}</div>
          <h3>{{ c.title }}</h3>
          <p>{{ c.body }}</p>
          <span class="tag">{{ c.tag }}</span>
        </article>
        {% endfor %}
      </div>
    </div>
  </section>

  <section>
    <div class="wrap">
      <div class="kicker">Self-verifying generation</div>
      <h2>It has to pass its own test before you see it</h2>
      <p class="sub">The interesting part isn't that it can write an exercise &mdash; it's that a
        generated exercise has to survive the same grader you do before it's allowed into the
        curriculum.</p>
      <div class="steps">
        {% for title, body in pipeline %}
        <div class="step">
          <h4>{{ title }}</h4>
          <p>{{ body }}</p>
        </div>
        {% endfor %}
      </div>
    </div>
  </section>

  <section>
    <div class="wrap">
      <div class="kicker">Discord</div>
      <h2>Eight commands</h2>
      <p class="sub">The same brain, same database, different surface &mdash; whatever you solve
        in Discord shows up in the dashboard and the CLI.</p>
      <div class="cmds">
        {% for name, desc in commands %}
        <div class="cmd"><code>{{ name }}</code><span>{{ desc }}</span></div>
        {% endfor %}
      </div>
    </div>
  </section>

  <section>
    <div class="wrap">
      <div class="kicker">Under the hood</div>
      <h2>Local-first by design</h2>
      <p class="sub">Nothing leaves the machine. Two models, picked for two different jobs.</p>
      <div class="stack">
        <div class="stat"><div class="v">llama3.2:3b</div><div class="k">Prose &mdash; Q&amp;A, explanations, lessons. Fast enough to feel conversational.</div></div>
        <div class="stat"><div class="v">llama3</div><div class="k">Structured generation &mdash; more reliable at emitting valid JSON and working Python.</div></div>
        <div class="stat"><div class="v">localhost:11434</div><div class="k">Ollama, on your hardware. No API key, no request leaves the box.</div></div>
        <div class="stat"><div class="v">curriculum.db</div><div class="k">One SQLite file shared by the bot, the dashboard and the CLI.</div></div>
      </div>
    </div>
  </section>
</main>

<footer>
  <div class="wrap">Raybot &mdash; Discord bot, web dashboard and CLI over one shared Python curriculum.</div>
</footer>

<script>
document.addEventListener('DOMContentLoaded', function () {
  // Custom cursor (a lerped, rotation-aware pointer)
  if (window.matchMedia('(pointer: fine)').matches) {
    var cur = document.createElement('div');
    cur.id = 'smooth-cursor';
    cur.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<path d="M4 2L20 12L12 13.5L9 21L4 2Z" fill="#f59e0b" stroke="#100c0a" stroke-width="1.2" stroke-linejoin="round"/></svg>';
    document.body.appendChild(cur);
    var mx = innerWidth / 2, my = innerHeight / 2, cx = mx, cy = my, ang = 0, lx = mx, ly = my;
    document.addEventListener('mousemove', function (e) { mx = e.clientX; my = e.clientY; });
    (function loop() {
      cx += (mx - cx) * 0.22;
      cy += (my - cy) * 0.22;
      var dx = mx - lx, dy = my - ly;
      if (Math.hypot(dx, dy) > 1) { ang += (Math.atan2(dy, dx) * 180 / Math.PI + 90 - ang) * 0.25; }
      lx = mx; ly = my;
      cur.style.transform = 'translate(' + cx + 'px,' + cy + 'px) rotate(' + ang + 'deg)';
      requestAnimationFrame(loop);
    })();
  }

  // Scramble-reveal on hover
  var CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  document.querySelectorAll('.hyper').forEach(function (el) {
    var original = el.textContent, running = false;
    el.addEventListener('mouseenter', function () {
      if (running) return;
      running = true;
      var start = performance.now(), len = original.length;
      (function frame(now) {
        var p = Math.min((now - start) / 500, 1), reveal = p * len, out = '';
        for (var i = 0; i < len; i++) {
          var ch = original[i];
          out += ch === ' ' ? ' ' : (i <= reveal ? ch.toUpperCase() : CHARS[Math.floor(Math.random() * CHARS.length)]);
        }
        el.textContent = out;
        if (p < 1) requestAnimationFrame(frame);
        else { el.textContent = original.toUpperCase(); running = false; }
      })(start);
    });
  });

  // Reveal sections on scroll
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { e.target.style.opacity = 1; e.target.style.transform = 'none'; io.unobserve(e.target); }
    });
  }, { threshold: 0.12 });
  document.querySelectorAll('section .wrap > *').forEach(function (el, i) {
    el.style.opacity = 0;
    el.style.transform = 'translateY(14px)';
    el.style.transition = 'opacity 0.5s ease ' + (i % 4 * 0.05) + 's, transform 0.5s ease ' + (i % 4 * 0.05) + 's';
    io.observe(el);
  });
});
</script>
</body>
</html>
"""


@app.route("/")
def showcase():
    return render_template_string(
        PAGE,
        hero_video=HERO_VIDEO,
        capabilities=CAPABILITIES,
        pipeline=PIPELINE,
        commands=COMMANDS,
    )


if __name__ == "__main__":
    app.run(port=5002, debug=False)
