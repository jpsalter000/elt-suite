"""Deterministic seeded dataset for the Helpline mock API.

Each record carries a hidden ``_committed_at`` (UTC): the moment the change
became visible in the feed. ``updated_at`` is stamped when the edit *started*,
up to ~25 minutes earlier, and rendered in the agent's local UTC offset. Keys
starting with ``_`` are never served.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 9, 1, tzinfo=UTC)
OFFSETS = [
    timezone(timedelta(hours=h, minutes=m)) for h, m in [(-7, 0), (-5, 0), (0, 0), (1, 0), (5, 30)]
]

NAMES = ["Ari Cohen", "Bea Lind", "Cyrus Park", "Dana Ivers", "Eli Moreau", "Fay Adler",
         "Gus Obi", "Hana Ito", "Ian Rees", "Jo Vance", "Kai Lund", "Lia Ferro",
         "Max Duval", "Nia Grant", "Oli Stone", "Pia Kerr"]  # fmt: skip
GROUPS = ["Tier 1", "Tier 2", "Escalations"]
TITLES = [
    "Cannot sign in", "Charged twice", "Sync is stuck", "How do I export?",
    "App crashes on launch", "Change billing email", "Webhook retries", "Slow dashboard",
]  # fmt: skip
STATES = ["new", "open", "on_hold", "resolved", "closed"]
LABELS = ["api", "billing", "bug", "vip", "mobile", "sso"]


def iso_local(moment: datetime, tz: timezone) -> str:
    return moment.astimezone(tz).isoformat(timespec="seconds")


def _between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    seconds = max(0, int((end - start).total_seconds()))
    return start + timedelta(seconds=rng.randint(0, seconds))


def _lag(rng: random.Random) -> timedelta:
    """Most edits commit within seconds; some sit open for up to 25 minutes."""
    if rng.random() < 0.7:
        return timedelta(seconds=rng.randint(0, 90))
    return timedelta(minutes=rng.randint(2, 25))


def generate(seed: int = 42, cases: int = 400) -> dict[str, list[dict]]:
    rng = random.Random(seed)

    staff: list[dict[str, Any]] = []
    for name in NAMES:
        committed = _between(rng, START, END)
        staff.append(
            {
                "staff_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                "display_name": name,
                "email": f"{name.split()[0].lower()}@hooli.example",
                "group": rng.choice(GROUPS),
                "status": "active" if rng.random() > 0.15 else "deactivated",
                "updated_at": iso_local(committed - _lag(rng), rng.choice(OFFSETS)),
                "_committed_at": committed,
            }
        )

    rows: list[dict[str, Any]] = []
    for n in range(1, cases + 1):
        tz = rng.choice(OFFSETS)
        opened = _between(rng, START, END - timedelta(days=2))
        committed = _between(rng, opened + timedelta(minutes=30), END)
        state = rng.choice(STATES)
        owner = None if rng.random() < 0.2 else rng.choice(staff)
        rows.append(
            {
                "case_number": f"HL-{n:06d}",
                "title": rng.choice(TITLES),
                "state": state,
                "urgency": rng.choice(["P1", "P2", "P3", "P3", "P4"]),
                "channel": rng.choice(["email", "chat", "phone"]),
                "contact": {"email": f"user{rng.randint(1, 250)}@example.org"},
                "owner": None
                if owner is None
                else {"staff_id": owner["staff_id"], "name": owner["display_name"]},
                "labels": ";".join(rng.sample(LABELS, k=rng.randint(0, 3))),
                "csat": {"rating": rng.choice(["good", "good", "bad"])}
                if state in ("resolved", "closed") and rng.random() < 0.6
                else None,
                "effort_minutes": rng.randint(0, 240),
                "opened_at": iso_local(opened, tz),
                "updated_at": iso_local(committed - _lag(rng), tz),
                "deleted": rng.random() < 0.05,
                "_committed_at": committed,
            }
        )
    return {"cases": rows, "staff": staff}
