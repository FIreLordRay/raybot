# Getting started

You've been added to this repo — here's how to get Raybot running on your own machine.
Nobody shares a server; everyone runs their own copy and gets their own progress.

## 1. Clone it

```bash
git clone https://github.com/FIreLordRay/raybot.git
cd raybot
```

`curriculum.db` comes committed in the repo, so you already have the full curriculum
(93 problems, including the Medline-project-specific track) the moment you clone —
nothing to download or generate.

## 2. Set up Python

Needs Python 3.8+.

```bash
python -m venv .venv
```

Activate it — this step differs by shell:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd.exe)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate
```

Then install the three packages Raybot actually needs:

```bash
pip install flask discord.py python-dotenv
```

## 3. Run it

```bash
# Terminal practice tool
python practice.py today

# Web dashboard — open http://localhost:5001
python dashboard.py
```

Both use your OS login as your identity, so your progress, streak, and badges are
automatically yours and separate from everyone else's.

## What you don't need

- **`.env` / Discord bot token** — only needed to run `discord_raybot.py` itself. If
  you're not running the bot, skip it entirely.
- **Ollama** — only needed for "Ask Raybot" tutoring and generating brand-new problems
  on demand. All 93 curated problems work without it. If you do want it, see the
  main [README](README.md#setup) for the model split it expects.

## If something's wrong

If the dashboard doesn't show all 8 `medline:` categories, you're either on an old
clone or something didn't install — re-run `git pull` and the `pip install` line above.
