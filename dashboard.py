"""Local progress dashboard for the Python practice curriculum + Raybot quizzes.

Reads/writes the same shared curriculum.db as practice.py and discord_raybot.py.
Also hosts its own interactive quiz — pick a category and/or difficulty,
answer 5 questions right in the browser, get graded instantly, and see a short
lesson on the concept before each exercise (Exercism-style). That attempt gets
recorded to the same quiz_history as Discord's !firelordray. The curriculum
auto-grows: if a category/difficulty runs out, Ollama generates a new problem
and verifies it before you ever see it.

Run it and open http://localhost:5001 in a browser:

    python dashboard.py

Security note: like Raybot's !quiz/!firelordray, the quiz here executes
whatever code you submit to grade it. Since this only listens on localhost
(nothing outside your machine can reach it), that's just you running your own
code — no different from running practice.py test yourself.
"""

import datetime
import getpass
import random
import threading

from flask import Flask, flash, jsonify, redirect, render_template_string, request, session, url_for

import badges  # achievement badges — shared with discord_raybot.py
import db
import generator
import grading
import tutor  # ask_raybot, explain_failure — shared with discord_raybot.py

db.init_db()

app = Flask(__name__)
app.secret_key = "raybot-dashboard-local-only"  # only ever served on localhost
app.permanent_session_lifetime = datetime.timedelta(days=365)

BUCKETS = ["easy", "medium", "hard"]
QUIZ_LENGTH = 5
GRADE_TIMEOUT_SECONDS = 5
# Defaults to this machine's Windows login — the same identity practice.py
# uses — so the CLI and dashboard share one identity for the primary user on
# their own machine with zero setup. Anyone else (or a shared/borrowed
# browser) can still pick their own name via the navbar widget.
DEFAULT_USER_NAME = getpass.getuser()


def get_user_name():
    """Each browser gets its own lightweight identity (no password — just a
    display name), stored in a long-lived session cookie. This is what makes
    the dashboard usable by more than one person: everyone who opens it picks
    a name once, and their solved/streak/badges stay separate from anyone
    else's from then on."""
    return session.get("user_name") or DEFAULT_USER_NAME


def render_page(template, **context):
    """Renders a page template, auto-filling user_name/streak/nav_active so
    every route doesn't have to repeat that boilerplate."""
    user_name = context.setdefault("user_name", get_user_name())
    context.setdefault("nav_active", None)
    context.setdefault("streak", db.get_streak(user_name))
    return render_template_string(template, **context)

STYLE = """
<style>
  :root {
    --bg: #0d0c14;
    --bg-alt: #1c1b2a;
    --card: #16151f;
    --card-border: #2a2838;
    --text: #f0eef7;
    --muted: #8f8ca3;
    --accent: #7c6cf5;
    --accent-dark: #6152d9;
    --accent-soft: #251f47;
    --good: #34d399;
    --warn: #fbbf24;
    --bad: #f87171;
    --navy: #241a44;
  }
  * { box-sizing: border-box; }
  html { background: var(--bg); }
  body {
    margin: 0;
    min-height: 100vh;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  .ribbon {
    background: var(--navy);
    color: #fff;
    text-align: center;
    font-size: 0.82rem;
    padding: 8px 16px;
  }
  .navbar {
    background: var(--card);
    border-bottom: 1px solid var(--card-border);
    padding: 14px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }
  .navbar-left { display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }
  .navbar .logo {
    font-weight: 800;
    font-size: 1.15rem;
    letter-spacing: -0.02em;
    color: var(--text);
    white-space: nowrap;
  }
  .navbar .logo .mark { color: var(--accent); }
  .navlinks { display: flex; gap: 18px; flex-wrap: wrap; }
  .navlinks a { color: var(--muted); font-weight: 600; font-size: 0.86rem; }
  .navlinks a:hover { color: var(--text); text-decoration: none; }
  .navlinks a.active { color: var(--accent); }
  .navbar .streak-badge {
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 700;
    padding: 7px 16px;
    border-radius: 999px;
    font-size: 0.85rem;
    white-space: nowrap;
  }
  .whoami-form { display: flex; align-items: center; gap: 6px; }
  .whoami-form input {
    width: 110px;
    margin: 0;
    padding: 6px 12px;
    font-size: 0.82rem;
    background: var(--bg-alt);
    border: 1px solid var(--card-border);
    border-radius: 999px;
    color: var(--text);
  }

  .container { max-width: 1080px; margin: 0 auto; padding: 32px 24px 80px; }
  .two-col { display: grid; grid-template-columns: 1.6fr 1fr; gap: 20px; align-items: start; }
  @media (max-width: 820px) { .two-col { grid-template-columns: 1fr; } }

  h1 { font-size: 1.7rem; font-weight: 800; letter-spacing: -0.02em; margin: 0 0 6px; }
  h2 { font-size: 1.05rem; font-weight: 700; margin: 0 0 14px; }
  h2.kicker {
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.75rem;
  }
  .sub { color: var(--muted); font-size: 0.92rem; margin-bottom: 28px; }

  .card {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 18px;
    padding: 22px 24px;
    box-shadow: 0 1px 2px rgba(32,19,71,0.03), 0 14px 28px -20px rgba(32,19,71,0.18);
  }

  .quickstart-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
  }
  .quickstart-card {
    display: block;
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 18px;
    padding: 24px 16px;
    text-align: center;
    color: var(--text);
    box-shadow: 0 1px 2px rgba(32,19,71,0.03);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
  }
  .quickstart-card:hover { transform: translateY(-2px); box-shadow: 0 14px 26px -16px rgba(32,19,71,0.28); text-decoration: none; }
  .quickstart-icon { font-size: 1.9rem; margin-bottom: 10px; }
  .quickstart-label { font-weight: 700; font-size: 0.9rem; }

  .stat-value { font-size: 2rem; font-weight: 800; letter-spacing: -0.02em; }
  .stat-label { color: var(--muted); font-size: 0.82rem; margin-top: 4px; }
  .bar-track { background: var(--bg-alt); border-radius: 999px; height: 8px; overflow: hidden; margin-top: 14px; }
  .bar-fill { background: var(--accent); height: 100%; border-radius: 999px; }

  .section { margin-top: 20px; }
  .track-row { display: flex; align-items: center; gap: 14px; padding: 12px 0; border-bottom: 1px solid var(--card-border); }
  .track-row:last-child { border-bottom: none; padding-bottom: 0; }
  .track-row:first-child { padding-top: 0; }
  .track-row.link { cursor: pointer; }
  .track-row.link:hover { text-decoration: none; }
  .track-icon {
    width: 38px; height: 38px; border-radius: 11px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; color: #fff; font-size: 0.85rem; flex-shrink: 0;
  }
  .track-icon.easy { background: linear-gradient(135deg, #4ade80, #16a34a); }
  .track-icon.medium { background: linear-gradient(135deg, #fbbf24, #d97706); }
  .track-icon.hard { background: linear-gradient(135deg, #f87171, #dc2626); }
  .track-icon.category { background: linear-gradient(135deg, #818cf8, #5848eb); }
  .track-info { flex: 1; min-width: 0; }
  .track-name { font-weight: 700; text-transform: capitalize; font-size: 0.9rem; margin-bottom: 6px; }
  .track-bar-track { background: var(--bg-alt); border-radius: 999px; height: 6px; overflow: hidden; }
  .track-bar-fill { height: 100%; border-radius: 999px; }
  .track-bar-fill.easy { background: #22c55e; }
  .track-bar-fill.medium { background: #f59e0b; }
  .track-bar-fill.hard { background: #ef4444; }
  .track-bar-fill.category { background: #5848eb; }
  .track-count { color: var(--muted); font-size: 0.78rem; margin-top: 6px; }

  table { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--card-border); }
  th { color: var(--muted); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
  tr:last-child td { border-bottom: none; }
  .empty { color: var(--muted); font-size: 0.9rem; padding: 8px 0; }

  .btn {
    display: inline-block;
    background: var(--accent);
    color: #fff;
    font-weight: 700;
    text-decoration: none;
    padding: 12px 24px;
    border-radius: 10px;
    border: none;
    cursor: pointer;
    font-size: 0.92rem;
    transition: background 0.15s ease;
  }
  .btn:hover { background: var(--accent-dark); text-decoration: none; }
  .btn.secondary { background: var(--card); color: var(--text); border: 1px solid var(--card-border); }
  .btn.secondary:hover { background: var(--bg-alt); }

  .diff-choices { display: flex; gap: 10px; flex-wrap: wrap; margin: 16px 0; }
  .diff-choices label {
    border: 1px solid var(--card-border);
    background: var(--card);
    border-radius: 999px;
    padding: 10px 18px;
    cursor: pointer;
    font-size: 0.9rem;
  }
  .diff-choices label:hover { background: var(--bg-alt); }
  .diff-choices input { margin-right: 6px; accent-color: var(--accent); }

  select {
    width: 100%;
    background: var(--card);
    color: var(--text);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 12px 14px;
    font-size: 0.92rem;
    margin: 10px 0 16px;
  }

  textarea, input[type="text"] {
    width: 100%;
    background: #0b0a12;
    color: var(--text);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 14px;
    font-size: 0.95rem;
    outline: none;
    transition: border-color 0.15s ease;
  }
  textarea:focus, input[type="text"]:focus { border-color: var(--accent); }
  textarea { min-height: 160px; font-family: "SF Mono", Consolas, monospace; resize: vertical; }
  .prompt { margin: 10px 0 18px; line-height: 1.6; color: var(--text); }
  .lesson {
    background: var(--accent-soft);
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 16px;
    font-size: 0.9rem;
    line-height: 1.6;
    color: var(--text);
  }
  .lesson .kicker { margin-bottom: 6px; }

  .progress-dots { display: flex; gap: 7px; margin-bottom: 20px; }
  .progress-dots .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--bg-alt); }
  .progress-dots .dot.done { background: var(--good); }
  .progress-dots .dot.current { background: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }

  .feedback { padding: 12px 16px; border-radius: 12px; margin-bottom: 18px; font-size: 0.9rem; border: 1px solid transparent; white-space: pre-line; line-height: 1.5; }
  .feedback.ok { background: #12291f; color: #4ade80; border-color: #1f4a35; }
  .feedback.bad { background: #2c1418; color: #f87171; border-color: #4a2028; }
  .feedback.info { background: var(--accent-soft); color: #b7aaff; border-color: #3a3160; }

  .badge-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
  .badge {
    border-radius: 14px;
    padding: 14px;
    text-align: center;
    border: 1px solid var(--card-border);
  }
  .badge.earned { background: linear-gradient(160deg, #2e2410, #201d2b); border-color: #6b5620; }
  .badge.locked { background: var(--bg-alt); opacity: 0.55; filter: grayscale(0.4); }
  .badge-emoji { font-size: 1.8rem; margin-bottom: 6px; }
  .badge-name { font-weight: 700; font-size: 0.82rem; }
  .badge-desc { color: var(--muted); font-size: 0.72rem; margin-top: 4px; line-height: 1.3; }

  .leaderboard-row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--card-border); }
  .leaderboard-row:last-child { border-bottom: none; }
  .leaderboard-rank { width: 28px; font-weight: 800; text-align: center; }
  .leaderboard-name { flex: 1; font-weight: 600; }
  .leaderboard-count { color: var(--muted); font-size: 0.85rem; }

  .heatmap { display: flex; gap: 3px; flex-wrap: wrap; }
  .heatmap-cell { width: 11px; height: 11px; border-radius: 3px; background: var(--bg-alt); }
  .heatmap-cell.l1 { background: #2f2a52; }
  .heatmap-cell.l2 { background: #453d7a; }
  .heatmap-cell.l3 { background: #5c4fa8; }
  .heatmap-cell.l4 { background: var(--accent); }

  .celebrate { animation: pop 0.4s ease; }
  @keyframes pop { 0% { transform: scale(0.9); opacity: 0; } 60% { transform: scale(1.03); } 100% { transform: scale(1); opacity: 1; } }
  .confetti-banner {
    background: linear-gradient(135deg, #2e2410, #34182b);
    border: 1px solid #6b5620;
    border-radius: 14px;
    padding: 16px;
    text-align: center;
    font-weight: 700;
    font-size: 1.05rem;
    margin-bottom: 18px;
    color: var(--text);
  }
  .badge-unlock {
    background: linear-gradient(135deg, #2e2410, #201d2b);
    border: 1px solid #6b5620;
    border-radius: 12px;
    padding: 10px 14px;
    margin-bottom: 10px;
    font-size: 0.9rem;
    color: var(--text);
  }

  .filter-bar { display: flex; gap: 10px; flex-wrap: wrap; }
  .filter-bar select { width: auto; min-width: 150px; margin: 0; }
  .status-icon { font-size: 1rem; }
  .btn-sm { padding: 6px 14px; font-size: 0.8rem; border-radius: 8px; }
  .code-preview {
    background: #0b0a12;
    border: 1px solid var(--card-border);
    border-radius: 8px;
    padding: 10px 12px;
    font-family: "SF Mono", Consolas, monospace;
    font-size: 0.8rem;
    color: var(--muted);
    white-space: pre-wrap;
    max-height: 100px;
    overflow-y: auto;
    margin-top: 6px;
  }
  .submission-row { padding: 12px 0; border-bottom: 1px solid var(--card-border); }
  .submission-row:last-child { border-bottom: none; }
  .submission-meta { display: flex; align-items: center; gap: 10px; font-size: 0.85rem; }
  .result-pill { padding: 3px 10px; border-radius: 999px; font-size: 0.72rem; font-weight: 700; }
  .result-pill.pass { background: #12291f; color: #4ade80; }
  .result-pill.fail { background: #2c1418; color: #f87171; }
</style>
"""

NAVBAR = """
<div class="ribbon">Small daily reps beat cramming &mdash; keep your streak alive.</div>
<div class="navbar">
  <div class="navbar-left">
    <div class="logo"><span class="mark">&lt;/&gt;</span> Raybot</div>
    <nav class="navlinks">
      <a class="{{ 'active' if nav_active == 'home' else '' }}" href="{{ url_for('dashboard') }}">Home</a>
      <a class="{{ 'active' if nav_active == 'progress' else '' }}" href="{{ url_for('progress_page') }}">Progress</a>
      <a class="{{ 'active' if nav_active == 'problems' else '' }}" href="{{ url_for('problems_page') }}">Problems</a>
      <a class="{{ 'active' if nav_active == 'categories' else '' }}" href="{{ url_for('categories_page') }}">Categories</a>
      <a class="{{ 'active' if nav_active == 'history' else '' }}" href="{{ url_for('history_page') }}">History</a>
      <a class="{{ 'active' if nav_active == 'badges' else '' }}" href="{{ url_for('badges_page') }}">Badges</a>
      <a class="{{ 'active' if nav_active == 'leaderboard' else '' }}" href="{{ url_for('leaderboard_page') }}">Leaderboard</a>
    </nav>
  </div>
  <div style="display: flex; align-items: center; gap: 12px;">
    <form method="post" action="{{ url_for('set_user') }}" class="whoami-form">
      <input type="text" name="user_name" value="{{ user_name }}" maxlength="40" title="Your display name — everyone's progress here is separate">
      <button class="btn secondary btn-sm" type="submit">Set</button>
    </form>
    {% if streak is defined %}<div class="streak-badge">&#128293; {{ streak }} day streak</div>{% endif %}
  </div>
</div>
<div class="container">
"""

HOME_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Practice Dashboard</title></head>
<body>
  <h1>Welcome back!</h1>
  <div class="sub">{{ solved }}/{{ total }} problems solved &middot; {{ pct }}% of curriculum &middot; {{ quiz_count }} quizzes taken</div>

  <div class="quickstart-grid">
    <a class="quickstart-card" href="{{ url_for('quiz_start_page') }}">
      <div class="quickstart-icon">&#128221;</div>
      <div class="quickstart-label">Take a quiz</div>
    </a>
    <a class="quickstart-card" href="{{ url_for('problems_page') }}">
      <div class="quickstart-icon">&#128269;</div>
      <div class="quickstart-label">Browse problems</div>
    </a>
    <a class="quickstart-card" href="{{ url_for('progress_page') }}">
      <div class="quickstart-icon">&#127919;</div>
      <div class="quickstart-label">Your progress</div>
    </a>
    <a class="quickstart-card" href="{{ url_for('categories_page') }}">
      <div class="quickstart-icon">&#128218;</div>
      <div class="quickstart-label">Categories</div>
    </a>
    <a class="quickstart-card" href="{{ url_for('history_page') }}">
      <div class="quickstart-icon">&#128202;</div>
      <div class="quickstart-label">Quiz history</div>
    </a>
    <a class="quickstart-card" href="{{ url_for('badges_page') }}">
      <div class="quickstart-icon">&#127942;</div>
      <div class="quickstart-label">Badges</div>
    </a>
    <a class="quickstart-card" href="{{ url_for('leaderboard_page') }}">
      <div class="quickstart-icon">&#129351;</div>
      <div class="quickstart-label">Leaderboard</div>
    </a>
  </div>

  <div class="card">
    <div class="stat-value">{{ streak }}</div>
    <div class="stat-label">Day streak</div>
    <div class="bar-track"><div class="bar-fill" style="width: {{ pct }}%;"></div></div>
    <div class="stat-label" style="margin-top: 8px;">{{ pct }}% of curriculum solved</div>
  </div>
</div>
</body>
</html>
"""

PROGRESS_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Your Progress</title></head>
<body>
  <h1>Your progress</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; back to dashboard</a></div>
  <div class="card" style="max-width: 600px;">
    {% for b in buckets %}
    <div class="track-row">
      <div class="track-icon {{ b.name }}">{{ b.name[0]|upper }}</div>
      <div class="track-info">
        <div class="track-name">{{ b.name }}</div>
        <div class="track-bar-track"><div class="track-bar-fill {{ b.name }}" style="width: {{ b.pct }}%;"></div></div>
        <div class="track-count">{{ b.solved }} / {{ b.total }} exercises completed</div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>
</body>
</html>
"""

CATEGORIES_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Categories</title></head>
<body>
  <h1>Categories</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; back to dashboard</a></div>
  <div class="card" style="max-width: 600px;">
    {% for c in categories %}
    <a class="track-row link" href="{{ url_for('problems_page', category=c.name) }}" style="color: inherit;">
      <div class="track-icon category">{{ c.name[0]|upper }}</div>
      <div class="track-info">
        <div class="track-name">{{ c.name }}</div>
        <div class="track-bar-track"><div class="track-bar-fill category" style="width: {{ c.pct }}%;"></div></div>
        <div class="track-count">{{ c.solved }} / {{ c.total }} exercises completed</div>
      </div>
    </a>
    {% endfor %}
  </div>
</div>
</body>
</html>
"""

HISTORY_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Quiz History</title></head>
<body>
  <h1>Quiz history</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; back to dashboard</a></div>

  <div class="card">
    <h2 class="kicker">Quiz score history</h2>
    {% if chart_points %}
    <svg viewBox="0 0 {{ chart_width }} 140" width="100%" height="140" preserveAspectRatio="none">
      <polyline points="{{ chart_points }}" fill="none" stroke="#7c6cf5" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
      {% for x, y, label in chart_dots %}
      <circle cx="{{ x }}" cy="{{ y }}" r="4" fill="#7c6cf5"><title>{{ label }}</title></circle>
      {% endfor %}
    </svg>
    {% else %}
    <div class="empty">No quizzes taken yet &mdash; click "Take a quiz" on the dashboard, or run !firelordray in Discord.</div>
    {% endif %}
  </div>

  <div class="card section">
    <h2 class="kicker">Activity (last 12 weeks)</h2>
    {% if activity_weeks %}
    <div style="display: flex; gap: 3px;">
      {% for week in activity_weeks %}
      <div class="heatmap" style="flex-direction: column;">
        {% for day in week %}
        <div class="heatmap-cell {{ day.level }}" title="{{ day.date }}: {{ day.count }} solved"></div>
        {% endfor %}
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty">No activity yet.</div>
    {% endif %}
  </div>

  <div class="card section">
    <h2 class="kicker">Recent quiz attempts</h2>
    {% if recent_quizzes %}
    <table>
      <tr><th>Date</th><th>Who</th><th>Difficulty</th><th>Category</th><th>Score</th></tr>
      {% for q in recent_quizzes %}
      <tr><td>{{ q.date }}</td><td>{{ q.user }}</td><td>{{ q.difficulty }}</td><td>{{ q.category }}</td><td>{{ q.score }}/{{ q.total }}</td></tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">Nothing yet.</div>
    {% endif %}
  </div>
</div>
</body>
</html>
"""

BADGES_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Badges</title></head>
<body>
  <h1>Badges ({{ earned_badges|length }}/{{ earned_badges|length + locked_badges|length }})</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; back to dashboard</a></div>
  <div class="card">
    <div class="badge-grid">
      {% for b in earned_badges %}
      <div class="badge earned">
        <div class="badge-emoji">{{ b.emoji }}</div>
        <div class="badge-name">{{ b.name }}</div>
        <div class="badge-desc">{{ b.desc }}</div>
      </div>
      {% endfor %}
      {% for b in locked_badges %}
      <div class="badge locked">
        <div class="badge-emoji">{{ b.emoji }}</div>
        <div class="badge-name">{{ b.name }}</div>
        <div class="badge-desc">{{ b.desc }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
</div>
</body>
</html>
"""

LEADERBOARD_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Leaderboard</title></head>
<body>
  <h1>Leaderboard</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; back to dashboard</a></div>
  <div class="card" style="max-width: 500px;">
    {% if leaderboard_rows %}
    {% for row in leaderboard_rows %}
    <div class="leaderboard-row">
      <div class="leaderboard-rank">{{ ['🥇','🥈','🥉'][loop.index0] if loop.index0 < 3 else loop.index }}</div>
      <div class="leaderboard-name">{{ row.user_name }}</div>
      <div class="leaderboard-count">{{ row.distinct_solved }} solved</div>
    </div>
    {% endfor %}
    {% else %}
    <div class="empty">Nobody's solved anything yet.</div>
    {% endif %}
  </div>
</div>
</body>
</html>
"""

PROBLEMS_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Problems</title></head>
<body>
  <h1>Problems</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; back to dashboard</a> &middot; {{ problems|length }} shown</div>

  <div class="card">
    <form method="get" action="{{ url_for('problems_page') }}" class="filter-bar">
      <select name="category" onchange="this.form.submit()">
        <option value="">All categories</option>
        {% for c in categories %}
        <option value="{{ c }}" {{ 'selected' if c == selected_category else '' }}>{{ c }}</option>
        {% endfor %}
      </select>
      <select name="difficulty" onchange="this.form.submit()">
        <option value="">All difficulties</option>
        <option value="easy" {{ 'selected' if selected_difficulty == 'easy' else '' }}>Easy</option>
        <option value="medium" {{ 'selected' if selected_difficulty == 'medium' else '' }}>Medium</option>
        <option value="hard" {{ 'selected' if selected_difficulty == 'hard' else '' }}>Hard</option>
      </select>
      <select name="status" onchange="this.form.submit()">
        <option value="">All statuses</option>
        <option value="solved" {{ 'selected' if selected_status == 'solved' else '' }}>Solved</option>
        <option value="unsolved" {{ 'selected' if selected_status == 'unsolved' else '' }}>Unsolved</option>
      </select>
    </form>
  </div>

  <div class="card section">
    {% if problems %}
    <table>
      <tr><th></th><th>Title</th><th>Difficulty</th><th>Category</th><th></th></tr>
      {% for p in problems %}
      <tr>
        <td class="status-icon">{{ '✅' if p.solved else ('⏭️' if p.skipped else '') }}</td>
        <td><a href="{{ url_for('problem_page', problem_id=p.id) }}">{{ p.title }}</a></td>
        <td>{{ p.bucket }}</td>
        <td>{{ p.category }}</td>
        <td><a class="btn secondary btn-sm" href="{{ url_for('problem_page', problem_id=p.id) }}">Solve</a></td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <div class="empty">No problems match those filters.</div>
    {% endif %}
  </div>
</div>
</body>
</html>
"""

PROBLEM_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>{{ problem.title }}</title></head>
<body>
  <h1>{{ problem.title }}{% if solved %} <span style="color: var(--good); font-size: 1.2rem;">&#9989;</span>{% endif %}</h1>
  <div class="sub">
    <a href="{{ url_for('problems_page') }}">&larr; back to problems</a>
    &middot; difficulty {{ problem.difficulty }} &middot; {{ problem.category }}
  </div>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for cat, message in messages %}
    <div class="feedback {{ cat }} {{ 'celebrate' if cat == 'ok' else '' }}">{{ message }}</div>
    {% endfor %}
  {% endwith %}

  <div class="card" style="max-width: 700px;">
    {% if problem.lesson %}
    <div class="lesson">
      <div class="kicker" style="text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; color: var(--accent-dark); font-weight: 700;">The idea</div>
      {{ problem.lesson }}
    </div>
    {% endif %}
    <div class="prompt">{{ problem.prompt }}</div>
    <form method="post" action="{{ url_for('problem_submit', problem_id=problem.id) }}">
      <textarea name="code" spellcheck="false">{{ code }}</textarea>
      <div style="margin-top: 12px; display: flex; gap: 10px;">
        <button class="btn" type="submit">Submit</button>
        <button class="btn secondary" type="button" onclick="getHint()">Get a hint</button>
      </div>
    </form>
    <div id="hint-answer" style="margin-top: 12px; white-space: pre-wrap; line-height: 1.5; color: var(--muted);"></div>
  </div>

  <div class="card" style="max-width: 700px; margin-top: 16px;">
    <h2 class="kicker" style="margin-top: 0;">Ask Raybot</h2>
    <div style="display: flex; gap: 8px;">
      <input id="ask-input" type="text" placeholder="What's a loop? Why isn't my code working?"
             onkeydown="if (event.key === 'Enter') askRaybot();">
      <button class="btn secondary" type="button" onclick="askRaybot()">Ask</button>
    </div>
    <div id="ask-answer" style="margin-top: 12px; white-space: pre-wrap; line-height: 1.5; color: var(--muted);"></div>
  </div>

  <div class="card section" style="max-width: 700px;">
    <h2 class="kicker">Your submissions</h2>
    {% if submissions %}
    {% for s in submissions %}
    <div class="submission-row">
      <div class="submission-meta">
        <span class="result-pill {{ 'pass' if s.passed == s.total else 'fail' }}">{{ 'PASS' if s.passed == s.total else 'FAIL' }}</span>
        <span>{{ s.passed }}/{{ s.total }} tests</span>
        <span style="color: var(--muted);">{{ s.created_at }}</span>
      </div>
    </div>
    {% endfor %}
    {% else %}
    <div class="empty">No submissions yet — solve it above to start your history.</div>
    {% endif %}
  </div>

  <script>
    async function askRaybot() {
      const input = document.getElementById('ask-input');
      const answerDiv = document.getElementById('ask-answer');
      const question = input.value.trim();
      if (!question) return;
      answerDiv.style.color = 'var(--muted)';
      answerDiv.textContent = 'Thinking...';
      try {
        const res = await fetch('{{ url_for("problem_ask", problem_id=problem.id) }}', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: question}),
        });
        const data = await res.json();
        answerDiv.style.color = 'var(--text)';
        answerDiv.textContent = data.answer;
      } catch (e) {
        answerDiv.style.color = 'var(--bad)';
        answerDiv.textContent = 'Something went wrong reaching Raybot.';
      }
    }
    async function getHint() {
      const hintDiv = document.getElementById('hint-answer');
      hintDiv.style.color = 'var(--muted)';
      hintDiv.textContent = 'Thinking...';
      try {
        const res = await fetch('{{ url_for("problem_hint", problem_id=problem.id) }}', { method: 'POST' });
        const data = await res.json();
        hintDiv.style.color = 'var(--text)';
        hintDiv.textContent = data.hint;
      } catch (e) {
        hintDiv.style.color = 'var(--bad)';
        hintDiv.textContent = 'Something went wrong reaching Raybot.';
      }
    }
  </script>
</div>
</body>
</html>
"""

QUIZ_START_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Take a Quiz</title></head>
<body>
  <h1>Take a quiz</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; back to dashboard</a></div>
  <div class="card" style="max-width: 500px;">
    <form method="post" action="{{ url_for('quiz_start') }}">
      <div>Pick a difficulty for your {{ quiz_length }}-question quiz:</div>
      <div class="diff-choices">
        <label><input type="radio" name="difficulty" value="" checked> Mixed</label>
        <label><input type="radio" name="difficulty" value="adaptive"> Adaptive 🎯</label>
        <label><input type="radio" name="difficulty" value="easy"> Easy</label>
        <label><input type="radio" name="difficulty" value="medium"> Medium</label>
        <label><input type="radio" name="difficulty" value="hard"> Hard</label>
      </div>
      <div class="sub" style="margin: -8px 0 0;">Adaptive picks more questions from whichever difficulty you've scored lowest on recently.</div>
      <div>Category (optional):</div>
      <select name="category">
        <option value="">Any category</option>
        {% for c in categories %}
        <option value="{{ c }}" {{ 'selected' if c == selected_category else '' }}>{{ c }}</option>
        {% endfor %}
      </select>
      <button class="btn" type="submit">Start</button>
    </form>
  </div>
  {% if empty_difficulty %}
  <div class="section"><div class="feedback bad">No matching problems available, and generating a new one failed &mdash; check that Ollama is running.</div></div>
  {% endif %}
</div>
</body>
</html>
"""

QUIZ_QUESTION_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Quiz</title></head>
<body>
  <h1>Question {{ index }} of {{ total }} &middot; {{ difficulty }}</h1>
  <div class="sub"><a href="{{ url_for('dashboard') }}">&larr; quit to dashboard</a></div>

  <div class="progress-dots">
    {% for i in range(total) %}
    <div class="dot {{ 'done' if i < index - 1 else ('current' if i == index - 1 else '') }}"></div>
    {% endfor %}
  </div>

  {% if generated_notice %}
  <div class="feedback info">{{ generated_notice }}</div>
  {% endif %}

  {% if feedback %}
  <div class="feedback {{ 'ok' if feedback.ok else 'bad' }}">{{ feedback.message }}</div>
  {% endif %}

  <div class="card" style="max-width: 700px;">
    <h2>{{ problem.title }} <span style="color: var(--muted); font-weight: normal;">(difficulty {{ problem.difficulty }}, {{ problem.category }})</span></h2>
    {% if problem.lesson %}
    <div class="lesson">
      <div class="kicker" style="text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; color: var(--accent-dark); font-weight: 700;">The idea</div>
      {{ problem.lesson }}
    </div>
    {% endif %}
    <div class="prompt">{{ problem.prompt }}</div>
    <form method="post" action="{{ url_for('quiz_submit') }}">
      <textarea name="code" spellcheck="false">{{ starter }}</textarea>
      <div style="margin-top: 12px;">
        <button class="btn" type="submit">Submit</button>
      </div>
    </form>
  </div>

  <div class="card" style="max-width: 700px; margin-top: 16px;">
    <h2 class="kicker" style="margin-top: 0;">Ask Raybot</h2>
    <div style="display: flex; gap: 8px;">
      <input id="ask-input" type="text" placeholder="What's a loop? Why isn't my code working?"
             onkeydown="if (event.key === 'Enter') askRaybot();">
      <button class="btn secondary" type="button" onclick="askRaybot()">Ask</button>
    </div>
    <div id="ask-answer" style="margin-top: 12px; white-space: pre-wrap; line-height: 1.5; color: var(--muted);"></div>
  </div>

  <script>
    async function askRaybot() {
      const input = document.getElementById('ask-input');
      const answerDiv = document.getElementById('ask-answer');
      const question = input.value.trim();
      if (!question) return;
      answerDiv.style.color = 'var(--muted)';
      answerDiv.textContent = 'Thinking...';
      try {
        const res = await fetch('{{ url_for("quiz_ask") }}', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: question}),
        });
        const data = await res.json();
        answerDiv.style.color = 'var(--text)';
        answerDiv.textContent = data.answer;
      } catch (e) {
        answerDiv.style.color = 'var(--bad)';
        answerDiv.textContent = 'Something went wrong reaching Raybot.';
      }
    }
  </script>
</div>
</body>
</html>
"""

QUIZ_RESULT_PAGE = STYLE + NAVBAR + """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Quiz Result</title></head>
<body>
  <h1 class="celebrate">Quiz done: {{ score }}/{{ total }}</h1>
  <div class="sub">{{ difficulty }} quiz{% if category %} &middot; {{ category }}{% endif %}</div>

  {% if is_perfect %}
  <div class="confetti-banner celebrate">🎉 Perfect score! Every single one, right on the money. 🎉</div>
  {% endif %}

  {% for badge in new_badges %}
  <div class="badge-unlock celebrate">{{ badge.emoji }} <strong>New badge unlocked: {{ badge.name }}</strong> — {{ badge.desc }}</div>
  {% endfor %}

  {% if feedback %}
  <div class="feedback {{ 'ok' if feedback.ok else 'bad' }}">{{ feedback.message }}</div>
  {% endif %}

  <div class="card" style="max-width: 500px;">
    <div>{{ comparison }}</div>
    <div style="margin-top: 16px; display: flex; gap: 10px;">
      <a class="btn" href="{{ url_for('quiz_start_page') }}">Take another</a>
      <a class="btn secondary" href="{{ url_for('dashboard') }}">Back to dashboard</a>
    </div>
  </div>
</div>
</body>
</html>
"""


def grade_with_timeout(problem, code, timeout=GRADE_TIMEOUT_SECONDS):
    code = grading.clean_code(code)
    result_holder = {}

    def target():
        result_holder["result"] = grading.exec_and_test(problem, code)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return 0, len(problem["tests"]), f"Timed out after {timeout}s (infinite loop?)."
    return result_holder.get("result", (0, len(problem["tests"]), "Unknown grading error."))


def generate_with_timeout(category, bucket, timeout=110):
    """Runs generator.generate_problem() (blocking Ollama calls) with a bound."""
    result_holder = {}

    def target():
        result_holder["result"] = generator.generate_problem(category, bucket=bucket)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    return result_holder.get("result")


@app.route("/set_user", methods=["POST"])
def set_user():
    name = (request.form.get("user_name") or "").strip()[:40]
    session.permanent = True
    session["user_name"] = name or DEFAULT_USER_NAME
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/")
def dashboard():
    user_name = get_user_name()
    total = len(db.all_problems())
    solved = len(db.get_solved_ids(user_name))
    pct = round((solved / total) * 100, 1) if total else 0
    quiz_count = len(db.get_quiz_history(user_name=user_name))

    return render_page(
        HOME_PAGE,
        solved=solved,
        total=total,
        pct=pct,
        quiz_count=quiz_count,
        nav_active="home",
    )


@app.route("/progress")
def progress_page():
    return render_page(
        PROGRESS_PAGE,
        buckets=db.difficulty_bucket_stats(get_user_name()),
        nav_active="progress",
    )


@app.route("/categories")
def categories_page():
    return render_page(
        CATEGORIES_PAGE,
        categories=db.category_stats(get_user_name()),
        nav_active="categories",
    )


@app.route("/history")
def history_page():
    user_name = get_user_name()
    history = db.get_quiz_history(user_name=user_name)

    recent = history[-15:][::-1]
    recent_quizzes = [
        {
            "date": h.get("date", "?"),
            "user": h.get("user_name", "you"),
            "difficulty": h.get("difficulty") or "mixed",
            "category": h.get("category") or "any",
            "score": h.get("score", 0),
            "total": h.get("total", 5),
        }
        for h in recent
    ]

    chart_series = history[-20:]
    chart_width = max(len(chart_series) - 1, 1) * 60 + 40
    chart_points = ""
    chart_dots = []
    if chart_series:
        pts = []
        for i, h in enumerate(chart_series):
            x = 20 + i * 60
            rate = (h.get("score", 0) / h.get("total", 5)) if h.get("total") else 0
            y = 130 - rate * 120
            pts.append(f"{x},{y}")
            chart_dots.append((x, y, f"{h.get('date', '?')}: {h.get('score', 0)}/{h.get('total', 5)}"))
        chart_points = " ".join(pts)

    activity = db.activity_by_day(user_name, days=84)
    for day in activity:
        c = day["count"]
        day["level"] = "" if c <= 0 else "l1" if c == 1 else "l2" if c == 2 else "l3" if c == 3 else "l4"
    activity_weeks = [activity[i:i + 7] for i in range(0, len(activity), 7)]

    return render_page(
        HISTORY_PAGE,
        recent_quizzes=recent_quizzes,
        chart_points=chart_points,
        chart_dots=chart_dots,
        chart_width=chart_width,
        activity_weeks=activity_weeks,
        nav_active="history",
    )


@app.route("/badges")
def badges_page():
    user_name = get_user_name()
    return render_page(
        BADGES_PAGE,
        earned_badges=badges.earned_badges(user_name),
        locked_badges=badges.locked_badges(user_name),
        nav_active="badges",
    )


@app.route("/leaderboard")
def leaderboard_page():
    return render_page(
        LEADERBOARD_PAGE,
        leaderboard_rows=db.leaderboard(limit=10),
        nav_active="leaderboard",
    )


@app.route("/problems")
def problems_page():
    user_name = get_user_name()
    category = request.args.get("category") or None
    difficulty = request.args.get("difficulty") or None
    status = request.args.get("status") or None

    pool = db.all_problems(category=category) if category else db.all_problems()
    if difficulty:
        pool = [p for p in pool if db.difficulty_bucket(p["difficulty"]) == difficulty]

    solved_ids = db.get_solved_ids(user_name)
    skipped_ids = db.get_skipped_ids(user_name)
    rows = []
    for p in pool:
        is_solved = p["id"] in solved_ids
        is_skipped = p["id"] in skipped_ids
        if status == "solved" and not is_solved:
            continue
        if status == "unsolved" and is_solved:
            continue
        rows.append({
            "id": p["id"],
            "title": p["title"],
            "category": p["category"],
            "bucket": db.difficulty_bucket(p["difficulty"]),
            "solved": is_solved,
            "skipped": is_skipped,
        })

    return render_page(
        PROBLEMS_PAGE,
        problems=rows,
        categories=[c["name"] for c in db.get_categories()],
        selected_category=category or "",
        selected_difficulty=difficulty or "",
        selected_status=status or "",
        nav_active="problems",
    )


@app.route("/problem/<problem_id>")
def problem_page(problem_id):
    user_name = get_user_name()
    problem = db.problem_by_id(problem_id)
    if problem is None:
        return redirect(url_for("problems_page"))
    code = db.last_submission_code(problem_id, user_name=user_name) or problem["starter"]
    return render_page(
        PROBLEM_PAGE,
        problem=problem,
        code=code,
        solved=problem_id in db.get_solved_ids(user_name),
        submissions=db.get_submissions(problem_id, user_name=user_name, limit=10),
        nav_active="problems",
    )


@app.route("/problem/<problem_id>/submit", methods=["POST"])
def problem_submit(problem_id):
    user_name = get_user_name()
    problem = db.problem_by_id(problem_id)
    if problem is None:
        return redirect(url_for("problems_page"))
    code = request.form.get("code", "")

    passed, total, error = grade_with_timeout(problem, code)
    db.record_submission(problem_id, code, passed, total, error=error, user_name=user_name)

    if error:
        flash(error, "bad")
    elif passed == total:
        before_badges = badges.earned_ids(user_name)
        db.mark_solved(problem_id, user_name=user_name, mode="dashboard_direct")
        flash(f"{tutor.praise()} ({passed}/{total} tests passed)", "ok")
        for badge in badges.newly_earned(before_badges, user_name):
            flash(f"{badge['emoji']} New badge unlocked: {badge['name']} — {badge['desc']}", "info")
    else:
        explanation = tutor.explain_failure(problem, code, passed, total)
        flash(f"Not quite — {passed}/{total} tests passed.\n\n{explanation}", "bad")

    return redirect(url_for("problem_page", problem_id=problem_id))


@app.route("/problem/<problem_id>/ask", methods=["POST"])
def problem_ask(problem_id):
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"answer": "Ask me something first!"})
    problem = db.problem_by_id(problem_id)
    answer = tutor.ask_raybot(question, problem)
    return jsonify({"answer": answer})


@app.route("/problem/<problem_id>/hint", methods=["POST"])
def problem_hint(problem_id):
    problem = db.problem_by_id(problem_id)
    if problem is None:
        return jsonify({"hint": "Problem not found."})
    if problem.get("hint"):
        return jsonify({"hint": problem["hint"]})
    hint = tutor.ask_raybot(
        f"Give me one short hint (1-2 sentences) for this exercise, without revealing the full solution "
        f"or writing any code: {problem['prompt']}",
        problem,
    )
    return jsonify({"hint": hint})


@app.route("/quiz")
def quiz_start_page():
    categories = [c["name"] for c in db.get_categories()]
    return render_page(
        QUIZ_START_PAGE,
        quiz_length=QUIZ_LENGTH,
        empty_difficulty=request.args.get("empty"),
        categories=categories,
        selected_category=request.args.get("category", ""),
    )


@app.route("/quiz/start", methods=["POST"])
def quiz_start():
    user_name = get_user_name()
    difficulty = request.form.get("difficulty") or None
    category = request.form.get("category") or None
    adaptive = difficulty == "adaptive"
    bucket_filter = None if adaptive else difficulty

    if category:
        pool = db.all_problems(category=category)
        if bucket_filter:
            pool = [p for p in pool if db.difficulty_bucket(p["difficulty"]) == bucket_filter]
    elif bucket_filter:
        pool = db.problems_in_bucket(bucket_filter)
    else:
        pool = db.all_problems()

    if adaptive:
        pool = db.adaptive_problem_order(user_name, pool)

    solved_ids = db.get_solved_ids(user_name)
    unsolved_ids = [p["id"] for p in pool if p["id"] not in solved_ids]
    quiz_ids = unsolved_ids[:QUIZ_LENGTH]
    generated_notice = None
    if len(quiz_ids) < QUIZ_LENGTH:
        solved_pool_ids = [p["id"] for p in pool if p["id"] in solved_ids]
        if not adaptive:
            random.shuffle(solved_pool_ids)
        quiz_ids += solved_pool_ids[: QUIZ_LENGTH - len(quiz_ids)]

    if not quiz_ids:
        fallback_bucket = db.adaptive_bucket_order(user_name)[0] if adaptive else (difficulty or "medium")
        new_id = generate_with_timeout(category or generator.pick_category(), fallback_bucket)
        if new_id:
            quiz_ids = [new_id]
            generated_notice = "Ran out of matching problems, so Raybot generated (and verified) a new one for you."
        else:
            return redirect(url_for("quiz_start_page", empty="1", category=category or ""))

    session["quiz_ids"] = quiz_ids
    session["quiz_index"] = 0
    session["quiz_score"] = 0
    session["quiz_difficulty"] = difficulty or "mixed"
    session["quiz_category"] = category or ""
    session["quiz_feedback"] = None
    session["quiz_generated_notice"] = generated_notice
    session["quiz_badges_before"] = list(badges.earned_ids(user_name))
    return redirect(url_for("quiz_question"), code=303)


@app.route("/quiz/question")
def quiz_question():
    quiz_ids = session.get("quiz_ids")
    if not quiz_ids:
        return redirect(url_for("quiz_start_page"))
    index = session.get("quiz_index", 0)
    if index >= len(quiz_ids):
        return redirect(url_for("quiz_result"))
    problem = db.problem_by_id(quiz_ids[index])
    feedback = session.pop("quiz_feedback", None)
    notice = session.pop("quiz_generated_notice", None)
    return render_page(
        QUIZ_QUESTION_PAGE,
        problem=problem,
        index=index + 1,
        total=len(quiz_ids),
        difficulty=session.get("quiz_difficulty", "mixed"),
        starter=problem["starter"],
        feedback=feedback,
        generated_notice=notice,
    )


@app.route("/quiz/ask", methods=["POST"])
def quiz_ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"answer": "Ask me something first!"})

    problem = None
    quiz_ids = session.get("quiz_ids")
    if quiz_ids:
        index = session.get("quiz_index", 0)
        if index < len(quiz_ids):
            problem = db.problem_by_id(quiz_ids[index])

    answer = tutor.ask_raybot(question, problem)
    return jsonify({"answer": answer})


@app.route("/quiz/submit", methods=["POST"])
def quiz_submit():
    user_name = get_user_name()
    quiz_ids = session.get("quiz_ids")
    if not quiz_ids:
        return redirect(url_for("quiz_start_page"))
    index = session.get("quiz_index", 0)
    problem = db.problem_by_id(quiz_ids[index])
    code = request.form.get("code", "")

    passed, total, error = grade_with_timeout(problem, code)
    db.record_submission(problem["id"], code, passed, total, error=error, user_name=user_name)

    if error:
        session["quiz_feedback"] = {"ok": False, "message": error}
    elif passed == total:
        session["quiz_score"] = session.get("quiz_score", 0) + 1
        db.mark_solved(problem["id"], user_name=user_name, mode="dashboard_quiz")
        session["quiz_feedback"] = {"ok": True, "message": f"{tutor.praise()} ({passed}/{total} tests passed)"}
    else:
        explanation = tutor.explain_failure(problem, code, passed, total)
        session["quiz_feedback"] = {
            "ok": False,
            "message": f"Not quite — {passed}/{total} tests passed.\n\n{explanation}",
        }

    session["quiz_index"] = index + 1
    if session["quiz_index"] >= len(quiz_ids):
        return redirect(url_for("quiz_result"), code=303)
    return redirect(url_for("quiz_question"), code=303)


@app.route("/quiz/result")
def quiz_result():
    user_name = get_user_name()
    quiz_ids = session.get("quiz_ids")
    if not quiz_ids:
        return redirect(url_for("quiz_start_page"))

    score = session.get("quiz_score", 0)
    total = len(quiz_ids)
    difficulty = session.get("quiz_difficulty", "mixed")
    category = session.get("quiz_category") or None
    feedback = session.get("quiz_feedback")

    same_bucket_past = db.quiz_history_by_bucket(difficulty, key="difficulty", user_name=user_name)
    past_scores = [h["score"] / h["total"] for h in same_bucket_past if h.get("total")]

    db.record_quiz_attempt(score, total, difficulty=difficulty, category=category, user_name=user_name)

    comparison = ""
    if past_scores:
        avg = sum(past_scores) / len(past_scores)
        rate = score / total if total else 0
        if rate > avg:
            comparison = f"That's better than your {difficulty} average so far — nice improvement!"
        elif rate < avg:
            comparison = f"A bit below your {difficulty} average — keep at it."
        else:
            comparison = f"Right on your {difficulty} average."

    badges_before = set(session.get("quiz_badges_before", []))
    new_badges = badges.newly_earned(badges_before, user_name)
    is_perfect = total > 0 and score == total

    for key in ("quiz_ids", "quiz_index", "quiz_score", "quiz_difficulty", "quiz_category",
                "quiz_feedback", "quiz_generated_notice", "quiz_badges_before"):
        session.pop(key, None)

    return render_page(
        QUIZ_RESULT_PAGE,
        score=score,
        total=total,
        difficulty=difficulty,
        category=category,
        feedback=feedback,
        comparison=comparison,
        new_badges=new_badges,
        is_perfect=is_perfect,
    )


if __name__ == "__main__":
    app.run(port=5001, debug=False)
