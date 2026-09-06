"""One-off batch: bring every category up to at least TARGET_PER_CATEGORY
problems by generating (and verifying) new ones with Ollama. Safe to re-run —
it only tops up categories that are still short.

    python grow_curriculum.py
"""

import time

import db
import generator

TARGET_PER_CATEGORY = 100
BUCKET_CYCLE = ["easy", "medium", "hard"]
# Safety valve: some categories (arithmetic-heavy ones especially) have a lower
# generation hit rate since verification rejects any problem whose own
# reference solution doesn't pass its own tests (a local 8B model occasionally
# does the arithmetic wrong when writing test cases). Without a cap, a category
# that's fundamentally hard to generate for could loop indefinitely trying to
# reach TARGET_PER_CATEGORY. This bounds total *attempts* (successes + failures)
# per category to a generous multiple of what's needed.
MAX_ATTEMPT_MULTIPLIER = 4


def main():
    db.init_db()
    added = 0
    failed = 0
    start = time.monotonic()
    for cat in db.get_categories():
        current = len(db.all_problems(category=cat["name"]))
        target_needed = max(0, TARGET_PER_CATEGORY - current)
        if target_needed == 0:
            print(f"{cat['name']}: already has {current}, skipping", flush=True)
            continue
        max_attempts_for_category = target_needed * MAX_ATTEMPT_MULTIPLIER
        print(f"{cat['name']}: has {current}, generating up to {target_needed} more...", flush=True)

        got_for_category = 0
        attempt = 0
        while got_for_category < target_needed and attempt < max_attempts_for_category:
            bucket = BUCKET_CYCLE[attempt % len(BUCKET_CYCLE)]
            item_start = time.monotonic()
            pid = generator.generate_problem(cat["name"], bucket=bucket)
            elapsed = time.monotonic() - item_start
            total_elapsed = time.monotonic() - start
            attempt += 1
            if pid:
                p = db.problem_by_id(pid)
                added += 1
                got_for_category += 1
                print(
                    f"  [{cat['name']} {got_for_category}/{target_needed}] added '{p['title']}' ({bucket}) "
                    f"in {elapsed:.0f}s — total added: {added}, total elapsed: {total_elapsed / 60:.1f}min",
                    flush=True,
                )
            else:
                failed += 1
                print(
                    f"  [{cat['name']} attempt {attempt}/{max_attempts_for_category}] "
                    f"generation failed after retries ({elapsed:.0f}s) — trying again",
                    flush=True,
                )

        if got_for_category < target_needed:
            print(
                f"  {cat['name']}: hit the attempt cap with only {got_for_category}/{target_needed} added — "
                f"moving on (re-run this script later to keep trying).",
                flush=True,
            )

    print(f"\nDone. Added {added} new problems, {failed} failed attempts, {(time.monotonic() - start) / 60:.1f} minutes total.", flush=True)
    print("\nFinal category counts:", flush=True)
    for c in db.category_stats():
        print(f"  {c['name']}: {c['total']}", flush=True)


if __name__ == "__main__":
    main()
