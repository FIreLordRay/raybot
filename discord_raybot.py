"""Raybot: Discord front-end for the Python practice curriculum.

Shares the same curriculum.db database as the practice.py CLI tool and the
dashboard (imported directly from the python-practice folder — see
PRACTICE_DIR below). The curriculum auto-grows: when a category/difficulty
runs out of curated problems, Ollama generates (and self-verifies) a new one.

Commands:
    !quiz            Post a race-mode problem in this channel. First person to
                      reply with a working solution (in a ```python code block```)
                      wins and it's marked solved for everyone.
    !firelordray [easy|medium|hard] [category]
                      DMs you a personal 5-question quiz (optionally filtered
                      by difficulty and/or category) and tracks your score over time.
                      Both are positional, in that order -- there's no way to give
                      only a category: `!firelordray strings` binds "strings" to
                      the difficulty slot and errors ("Difficulty must be easy,
                      medium, or hard"). To filter by category, give a difficulty
                      too, e.g. `!firelordray easy strings`, or leave both off.
    !setquizchannel   (Manage Server permission) Makes this channel the one
                      that gets a daily auto-posted race question.
    !progress         Shows shared curriculum progress/streak.
    !categories       Lists categories and per-category progress.
    !leaderboard      Top solvers, ranked by distinct problems solved.
    !badges           Your earned/locked achievement badges.
    !raybothelp       Lists these commands.

Setup required before running:
    1. In the Discord Developer Portal, enable the "MESSAGE CONTENT INTENT"
       privileged gateway intent for this bot.
    2. Add DISCORD_TOKEN=<your bot token> to PythonProject/.env
    3. Invite the bot to your server with Send Messages / Read Message
       History / Add Reactions permissions.
    4. Run: python discord_raybot.py   (keep this process running)

Security note: !quiz and !firelordray both execute code submitted by Discord
users (that's the point — it's how answers get graded). A denylist blocks the
most obviously dangerous patterns (file/network/process access) and a timeout
bounds slow submissions, but this is NOT a full sandbox — a determined bad
actor could still tie up the bot process. Only enable !quiz in servers/channels
with people you trust.
"""

import asyncio
import concurrent.futures
import datetime
import json
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# --- Shared curriculum + grading: import directly from the python-practice folder ---
PRACTICE_DIR = Path(r"C:\Users\rayra\Documents\python-practice")
sys.path.insert(0, str(PRACTICE_DIR))
import badges  # noqa: E402  (achievement badges — shared with dashboard.py)
import db  # noqa: E402
import generator  # noqa: E402
import grading  # noqa: E402  (extract_code, exec_and_test — shared with dashboard.py)
import tutor  # noqa: E402  (ask_raybot, explain_failure — shared with dashboard.py)

db.init_db()

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

CONFIG_FILE = ROOT / "raybot_config.json"
DEFAULT_QUIZ_HOUR = 9  # local machine time, 24h clock

EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)
EXEC_TIMEOUT_SECONDS = 5

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# channel_id -> {"problem_id": str, "started": float}
active_races = {}
race_locks = {}


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"quiz_channel_id": None, "quiz_hour": DEFAULT_QUIZ_HOUR, "last_auto_post_date": None}


def save_config(config):
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


extract_code = grading.extract_code


def _run_grading_in_subprocess(problem, code, result_queue):
    """Top-level (picklable) subprocess entry point -- see grade_submission."""
    try:
        result_queue.put(grading.exec_and_test(problem, code))
    except Exception as exc:  # noqa: BLE001 - must always put *something*, or the parent hangs
        result_queue.put((0, len(problem.get("tests") or []), f"{type(exc).__name__}: {exc}"))


def _grade_blocking(problem, code, timeout_seconds):
    """Runs grading.exec_and_test in its own OS process, killed on timeout.

    Was previously `loop.run_in_executor(EXECUTOR, grading.exec_and_test, ...)`
    with `asyncio.wait_for` around it -- that only stops *awaiting* the
    future; the ThreadPoolExecutor worker actually running an infinite-loop
    submission keeps burning that thread forever (Python cannot forcibly
    stop a thread). EXECUTOR has 4 workers total, shared with tutor
    explanations and problem generation, so 4 concurrent hung submissions
    (four different users, or one user four times) permanently wedge ALL of
    it -- every subsequent submission times out regardless of what it does,
    and Ollama-backed features stop responding too, forever, until the bot
    process is restarted. An OS process, unlike a thread, can actually be
    killed: this function always returns within timeout_seconds (plus a
    small terminate/join overhead) no matter what the submitted code does,
    which is what makes it safe to call from a normal thread pool without
    that pool ever wedging the way EXECUTOR could.
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(target=_run_grading_in_subprocess, args=(problem, code, result_queue), daemon=True)
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive():  # pragma: no cover - terminate() not honoured in time, rare
            process.kill()
            process.join(1)
        return 0, len(problem.get("tests") or []), f"Timed out after {timeout_seconds}s (infinite loop?)."
    try:
        return result_queue.get_nowait()
    except Exception:
        return 0, len(problem.get("tests") or []), "The submission process exited without reporting a result."


async def grade_submission(problem, code):
    loop = asyncio.get_running_loop()
    # Deliberately NOT run on EXECUTOR: this uses the loop's default executor,
    # whose threads always return promptly (see _grade_blocking's docstring)
    # regardless of what the submitted code does, so grading can never wedge
    # the pool tutor/generator calls also depend on.
    return await loop.run_in_executor(None, _grade_blocking, problem, code, EXEC_TIMEOUT_SECONDS)


async def explain_failure_async(problem, code, passed, total):
    """Runs tutor.explain_failure() (a blocking Ollama call) off the event loop."""
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(EXECUTOR, tutor.explain_failure, problem, code, passed, total)
        return await asyncio.wait_for(future, timeout=tutor.OLLAMA_TIMEOUT_SECONDS + 5)
    except asyncio.TimeoutError:
        return "(Raybot's explanation timed out — Ollama may be slow or busy right now.)"


async def next_or_generate_async(category=None, bucket="medium"):
    """Runs generator.next_or_generate() (can call Ollama) off the event loop."""
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(EXECUTOR, generator.next_or_generate, category, bucket)
    return await future


DIFFICULTY_COLOR = {
    "easy": discord.Color.green(),
    "medium": discord.Color.orange(),
    "hard": discord.Color.red(),
}


def format_problem_embed(problem, heading):
    bucket = db.difficulty_bucket(problem["difficulty"])
    embed = discord.Embed(
        title=heading,
        description=f"**{problem['title']}**",
        color=DIFFICULTY_COLOR.get(bucket, discord.Color.blurple()),
    )
    embed.add_field(name="Difficulty", value=f"{bucket} ({problem['difficulty']})", inline=True)
    embed.add_field(name="Category", value=problem["category"], inline=True)
    if problem.get("lesson"):
        embed.add_field(name="💡 The idea", value=problem["lesson"][:1024], inline=False)
    embed.add_field(name="Prompt", value=problem["prompt"][:1024], inline=False)
    embed.set_footer(text="Reply with a ```python code block``` containing your solution.")
    return embed


@bot.event
async def on_ready():
    print(f"Raybot logged in as {bot.user}")
    if not daily_quiz_task.is_running():
        daily_quiz_task.start()


@bot.event
async def on_command_error(ctx, error):
    """Without this, an exception anywhere inside a command coroutine was only
    printed to the host's own console -- the Discord user who ran the command
    saw nothing happen at all, with no way to tell a bug from a typo."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"{ctx.author.mention} Couldn't parse that — check `!raybothelp` for the right format.")
        return
    print(f"Unhandled error in command {ctx.command}: {type(error).__name__}: {error}")
    await ctx.send(f"{ctx.author.mention} Something went wrong running that command — it's been logged.")


@bot.command(name="quiz")
async def cmd_quiz(ctx):
    if ctx.channel.id in active_races:
        problem = db.problem_by_id(active_races[ctx.channel.id]["problem_id"])
        await ctx.send(f"There's already a race running here: **{problem['title']}**. Solve that one first!")
        return
    problem_id = db.next_unsolved()
    if problem_id is None:
        await ctx.send("Curriculum's exhausted — generating a new problem with Ollama (may take a minute)...")
        problem_id, _ = await next_or_generate_async()
    if problem_id is None:
        await ctx.send("Couldn't generate a new problem — check that Ollama is running.")
        return
    problem = db.problem_by_id(problem_id)
    active_races[ctx.channel.id] = {"problem_id": problem_id, "started": time.monotonic()}
    race_locks.setdefault(ctx.channel.id, asyncio.Lock())
    await ctx.send(embed=format_problem_embed(problem, "🏁 Race! First correct answer wins."))


@bot.command(name="firelordray")
async def cmd_firelordray(ctx, difficulty: str = None, category: str = None):
    author = ctx.author

    bucket = None
    if difficulty is not None:
        bucket = difficulty.strip().lower()
        if bucket not in ("easy", "medium", "hard"):
            await ctx.send(
                f"{author.mention} Difficulty must be `easy`, `medium`, or `hard` "
                f"(or leave it blank for a mixed quiz)."
            )
            return

    valid_categories = {c["name"] for c in db.get_categories()}
    if category and category not in valid_categories:
        await ctx.send(f"{author.mention} Unknown category `{category}`. Try `!categories` to see what's available.")
        return

    if category:
        pool = db.all_problems(category=category)
        if bucket:
            pool = [p for p in pool if db.difficulty_bucket(p["difficulty"]) == bucket]
    elif bucket:
        pool = db.problems_in_bucket(bucket)
    else:
        pool = db.all_problems()

    solved = db.get_solved_ids()
    unsolved = [p for p in pool if p["id"] not in solved]
    quiz_problems = unsolved[:5]
    if len(quiz_problems) < 5:
        solved_pool = [p for p in pool if p["id"] in solved]
        random.shuffle(solved_pool)
        quiz_problems += solved_pool[: 5 - len(quiz_problems)]

    if not quiz_problems:
        label = f"{bucket or ''} {category or ''}".strip() or "any"
        await ctx.send(f"{author.mention} No {label} problems available — try a different category/difficulty.")
        return

    label = bucket or "mixed"

    try:
        await author.send(
            f"**Your personal 5-question {label} quiz is starting!** "
            f"Reply here in DM with a \\`\\`\\`python code block\\`\\`\\` for each question. "
            f"You have {EXEC_TIMEOUT_SECONDS + 175}s per question."
        )
    except discord.Forbidden:
        await ctx.send(f"{author.mention} I can't DM you — check your privacy settings and try again.")
        return

    await ctx.send(f"{author.mention} Sent you a DM with your {label} quiz — go check!")

    score = 0
    for i, problem in enumerate(quiz_problems, start=1):
        lesson = f"\n{problem['lesson']}\n" if problem.get("lesson") else ""
        await author.send(
            f"**Question {i}/5: {problem['title']}** (difficulty {problem['difficulty']}, {problem['category']})\n"
            f"{lesson}\n{problem['prompt']}"
        )

        def check(m):
            return m.author.id == author.id and isinstance(m.channel, discord.DMChannel)

        try:
            reply = await bot.wait_for("message", check=check, timeout=180)
        except asyncio.TimeoutError:
            await author.send("Time's up on that one — moving on.")
            continue

        code = extract_code(reply.content)
        if code is None:
            await author.send("Didn't see a ```python code block``` in that — counting it as skipped.")
            continue

        passed, total, error = await grade_submission(problem, code)
        if error:
            await author.send(f"{error} ({passed}/{total} tests passed)")
        elif passed == total:
            score += 1
            before_badges = badges.earned_ids()
            db.mark_solved(problem["id"], user_name=str(author), mode="personal_quiz")
            await author.send(f"{tutor.praise()} ({passed}/{total} tests passed)")
            for badge in badges.newly_earned(before_badges):
                await author.send(f"{badge['emoji']} **New badge unlocked: {badge['name']}** — {badge['desc']}")
        else:
            explanation = await explain_failure_async(problem, code, passed, total)
            await author.send(f"Not quite — {passed}/{total} tests passed.\n\n{explanation}")

    # Read past attempts BEFORE recording this one -- reversed, this dropped
    # the *last row returned* on the assumption it was always this insert,
    # which only holds with zero concurrency. Any other quiz for the same
    # difficulty finishing in between (plausible: each question waits up to
    # 180s) means the dropped row belongs to someone else, and this user's
    # own new attempt stays counted in their own "average", quietly
    # inflating or deflating the very comparison being reported to them.
    # dashboard.py's quiz_result() already does this in the correct order.
    same_bucket_past = db.quiz_history_by_bucket(label, key="difficulty")
    past_scores = [h["score"] / h["total"] for h in same_bucket_past if h.get("total")]
    db.record_quiz_attempt(score, 5, difficulty=label, category=category, user_name=str(author))

    comparison = ""
    if past_scores:
        avg = sum(past_scores) / len(past_scores)
        this_rate = score / 5
        if this_rate > avg:
            comparison = f"That's better than your {label} average so far — nice improvement!"
        elif this_rate < avg:
            comparison = f"A bit below your {label} average — keep at it."
        else:
            comparison = f"Right on your {label} average."

    embed = discord.Embed(
        title="🎉 Quiz done!" if score == 5 else "Quiz done",
        description=f"**{score}/5** ({label})\n{comparison}",
        color=discord.Color.gold() if score == 5 else discord.Color.blurple(),
    )
    await author.send(embed=embed)


@bot.command(name="setquizchannel")
@commands.has_permissions(manage_guild=True)
async def cmd_setquizchannel(ctx):
    config = load_config()
    config["quiz_channel_id"] = ctx.channel.id
    save_config(config)
    await ctx.send(f"Daily quiz will now auto-post in #{ctx.channel.name} at {config['quiz_hour']}:00 (bot host's local time).")


@bot.command(name="progress")
async def cmd_progress(ctx):
    total = len(db.all_problems())
    solved = len(db.get_solved_ids())
    streak = db.get_streak()
    await ctx.send(f"Curriculum progress: {solved}/{total} solved. Current streak: {streak} day(s).")


@bot.command(name="categories")
async def cmd_categories(ctx):
    stats = db.category_stats()
    if not stats:
        await ctx.send("No categories yet.")
        return
    lines = [f"**{c['name']}**: {c['solved']}/{c['total']} ({c['pct']}%)" for c in stats]
    await ctx.send("**Categories**\n" + "\n".join(lines))


@bot.command(name="leaderboard")
async def cmd_leaderboard(ctx):
    rows = db.leaderboard(limit=10)
    if not rows:
        await ctx.send("Nobody's solved anything yet — run `!quiz` and be the first!")
        return
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title="🏆 Leaderboard", color=discord.Color.gold())
    lines = []
    for i, row in enumerate(rows):
        rank = medals[i] if i < len(medals) else f"{i + 1}."
        lines.append(f"{rank} **{row['user_name']}** — {row['distinct_solved']} solved")
    embed.description = "\n".join(lines)
    await ctx.send(embed=embed)


@bot.command(name="badges")
async def cmd_badges(ctx):
    earned = badges.earned_badges()
    locked = badges.locked_badges()
    embed = discord.Embed(title="🎖️ Badges", color=discord.Color.purple())
    if earned:
        embed.add_field(
            name=f"Earned ({len(earned)})",
            value="\n".join(f"{b['emoji']} **{b['name']}** — {b['desc']}" for b in earned),
            inline=False,
        )
    if locked:
        embed.add_field(
            name=f"Locked ({len(locked)})",
            value="\n".join(f"⬜ {b['name']} — {b['desc']}" for b in locked),
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.command(name="raybothelp")
async def cmd_help(ctx):
    await ctx.send(
        "**Raybot commands**\n"
        "`!quiz` — start a race-mode problem in this channel (first correct answer wins)\n"
        "`!firelordray [easy|medium|hard] [category]` — get your own personal 5-question quiz via DM\n"
        "`!categories` — list categories and your progress in each\n"
        "`!leaderboard` — top solvers across the server\n"
        "`!badges` — see which achievements you've earned (and which are still locked)\n"
        "`!setquizchannel` — (server managers) set this channel for the daily auto-quiz\n"
        "`!progress` — show shared curriculum progress and streak\n"
        "Reply to an active race with a ```python code block``` containing your solution."
    )


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    race = active_races.get(message.channel.id)
    if race is None:
        return
    code = extract_code(message.content)
    if code is None:
        return

    lock = race_locks.setdefault(message.channel.id, asyncio.Lock())
    async with lock:
        race = active_races.get(message.channel.id)
        if race is None:
            return  # someone else already won while we waited for the lock
        problem = db.problem_by_id(race["problem_id"])
        passed, total, error = await grade_submission(problem, code)
        if error:
            await message.add_reaction("⚠️")
            await message.channel.send(f"{message.author.mention} {error}")
            return
        if passed == total:
            elapsed = time.monotonic() - race["started"]
            before_badges = badges.earned_ids()
            db.mark_solved(problem["id"], user_name=str(message.author), mode="race")
            del active_races[message.channel.id]

            embed = discord.Embed(
                title="🏆 Race won!",
                description=f"{message.author.mention} solved it first.",
                color=discord.Color.gold(),
            )
            embed.add_field(name="Problem", value=problem["title"], inline=True)
            embed.add_field(name="Time", value=f"{elapsed:.1f}s", inline=True)
            embed.add_field(name="Tests", value=f"{passed}/{total}", inline=True)
            embed.set_footer(text="Run !quiz for the next one.")
            await message.channel.send(embed=embed)

            for badge in badges.newly_earned(before_badges):
                await message.channel.send(
                    f"{badge['emoji']} {message.author.mention} just unlocked **{badge['name']}** — {badge['desc']}"
                )
        else:
            await message.add_reaction("❌")


@tasks.loop(seconds=60)
async def daily_quiz_task():
    config = load_config()
    channel_id = config.get("quiz_channel_id")
    if channel_id is None:
        return
    now = datetime.datetime.now()
    if now.hour != config.get("quiz_hour", DEFAULT_QUIZ_HOUR):
        return
    today = db.today_str()
    if config.get("last_auto_post_date") == today:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        return
    if channel_id in active_races:
        config["last_auto_post_date"] = today
        save_config(config)
        return
    problem_id = db.next_unsolved()
    if problem_id is None:
        problem_id, _ = await next_or_generate_async()
    if problem_id is None:
        return
    problem = db.problem_by_id(problem_id)
    active_races[channel_id] = {"problem_id": problem_id, "started": time.monotonic()}
    race_locks.setdefault(channel_id, asyncio.Lock())
    await channel.send(embed=format_problem_embed(problem, "🌅 Daily race! First correct answer wins."))
    config["last_auto_post_date"] = today
    save_config(config)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Missing DISCORD_TOKEN in .env — add it and run again.")
        sys.exit(1)
    bot.run(token)
