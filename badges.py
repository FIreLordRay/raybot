"""Achievement badges — computed fresh from current db state every time, never
stored. That keeps them always consistent with whatever solve_log/progress/
quiz_history actually say, with no separate "badges earned" table to drift
out of sync.
"""

import db

BADGE_DEFS = [
    {"id": "first_steps", "emoji": "🌱", "name": "First Steps", "desc": "Solve your first problem"},
    {"id": "ten_solved", "emoji": "💪", "name": "10 Solved", "desc": "Solve 10 problems"},
    {"id": "twenty_five_solved", "emoji": "💪", "name": "25 Solved", "desc": "Solve 25 problems"},
    {"id": "fifty_solved", "emoji": "🏋️", "name": "50 Solved", "desc": "Solve 50 problems"},
    {"id": "hundred_solved", "emoji": "🏋️", "name": "100 Solved", "desc": "Solve 100 problems"},
    {"id": "streak_3", "emoji": "🔥", "name": "3-Day Streak", "desc": "Practice 3 days in a row"},
    {"id": "streak_7", "emoji": "🔥", "name": "Week Streak", "desc": "Practice 7 days in a row"},
    {"id": "streak_30", "emoji": "🔥", "name": "Month Streak", "desc": "Practice 30 days in a row"},
    {"id": "hard_mode", "emoji": "😈", "name": "Hard Mode", "desc": "Solve a hard-difficulty problem"},
    {"id": "perfect_quiz", "emoji": "🎯", "name": "Perfect Quiz", "desc": "Score 100% on a 5-question quiz"},
    {"id": "race_winner", "emoji": "🏆", "name": "Race Winner", "desc": "Win a Discord race (first correct answer)"},
    {"id": "category_master", "emoji": "📚", "name": "Category Master", "desc": "Solve every problem in a category"},
]

BADGE_BY_ID = {b["id"]: b for b in BADGE_DEFS}


def earned_ids(user_name):
    """The set of badge ids currently earned by this specific user."""
    solved = db.get_solved_ids(user_name)
    solved_count = len(solved)
    streak = db.get_streak(user_name)
    ids = set()

    if solved_count >= 1:
        ids.add("first_steps")
    if solved_count >= 10:
        ids.add("ten_solved")
    if solved_count >= 25:
        ids.add("twenty_five_solved")
    if solved_count >= 50:
        ids.add("fifty_solved")
    if solved_count >= 100:
        ids.add("hundred_solved")

    if streak >= 3:
        ids.add("streak_3")
    if streak >= 7:
        ids.add("streak_7")
    if streak >= 30:
        ids.add("streak_30")

    if any(p["id"] in solved and db.difficulty_bucket(p["difficulty"]) == "hard" for p in db.all_problems()):
        ids.add("hard_mode")

    if any(h.get("total") and h["score"] == h["total"] for h in db.get_quiz_history(user_name=user_name)):
        ids.add("perfect_quiz")

    if any(row["mode"] == "race" for row in db.get_solve_log(user_name=user_name)):
        ids.add("race_winner")

    for cat in db.category_stats(user_name):
        if cat["total"] >= 3 and cat["solved"] == cat["total"]:
            ids.add("category_master")
            break

    return ids


def earned_badges(user_name):
    ids = earned_ids(user_name)
    return [b for b in BADGE_DEFS if b["id"] in ids]


def locked_badges(user_name):
    ids = earned_ids(user_name)
    return [b for b in BADGE_DEFS if b["id"] not in ids]


def newly_earned(previous_ids, user_name):
    """Given a set of badge ids earned BEFORE some action, returns the badge
    defs earned NOW but not before — for 'new badge unlocked!' announcements
    right after a solve. Call earned_ids(user_name) before the action, do the
    solve, then pass that snapshot here."""
    current = earned_ids(user_name)
    return [BADGE_BY_ID[i] for i in current if i not in previous_ids]
