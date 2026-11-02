"""Per-source health tracking: surface silent breakage.

Counters live in the "health" section of seen_jobs.json (via
``SeenStore.health``) and catch two failure modes:

- a source that keeps erroring (5 consecutive failed runs), and
- a source that keeps "succeeding" with zero jobs for 7 days - the
  signature of a quiet ATS migration, where an abandoned board returns
  200 with an empty list forever.

Warnings are one-time: a per-condition "warned" flag is set when the
threshold is crossed and reset when the source recovers, so a broken
source produces exactly one Telegram warning, not one per run.
"""

from datetime import UTC, date, datetime

CONSECUTIVE_FAILURES_THRESHOLD = 5
ZERO_JOBS_DAYS_THRESHOLD = 7


def record_run(health: dict, stats: dict, today: date | None = None) -> list[str]:
    """Update per-source counters from one run's stats; return the warning
    messages (if any) that should be sent this run.

    ``stats`` maps a source label to {"fetched": int, "errors": int}.
    ``today`` is injectable for tests.
    """
    today = today or datetime.now(UTC).date()
    warnings = []

    # Drop counters for sources no longer in the config.
    for label in [label for label in health if label not in stats]:
        del health[label]

    for label, stat in stats.items():
        entry = health.setdefault(
            label,
            {
                "consecutive_failures": 0,
                "zero_since": None,
                "warned_failures": False,
                "warned_zero": False,
            },
        )

        if stat["errors"]:
            entry["consecutive_failures"] += 1
            if (
                entry["consecutive_failures"] >= CONSECUTIVE_FAILURES_THRESHOLD
                and not entry["warned_failures"]
            ):
                entry["warned_failures"] = True
                warnings.append(
                    f"{label} has failed {entry['consecutive_failures']} runs in a row. "
                    "Check the source config or the Actions logs."
                )
            continue

        entry["consecutive_failures"] = 0
        entry["warned_failures"] = False

        if stat["fetched"] == 0:
            if entry["zero_since"] is None:
                entry["zero_since"] = today.isoformat()
            days = (today - date.fromisoformat(entry["zero_since"])).days
            if days >= ZERO_JOBS_DAYS_THRESHOLD and not entry["warned_zero"]:
                entry["warned_zero"] = True
                warnings.append(
                    f"{label} has returned zero jobs since {entry['zero_since']} "
                    f"({days} days). The company may have migrated to a different ATS - "
                    "check where its Apply links point."
                )
        else:
            entry["zero_since"] = None
            entry["warned_zero"] = False

    return warnings
