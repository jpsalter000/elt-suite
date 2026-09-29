"""Deterministic seeded dataset for the Ticketdesk mock API.

Timestamps use Ticketdesk's own format, ``YYYY-MM-DD HH:MM:SS`` in UTC.
``modified_at`` is truncated to the hour (think bulk automation updates), so
several records often share one value; that is what makes an exclusive
``modified_after`` filter a real hazard for incremental loads.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

FORMAT = "%Y-%m-%d %H:%M:%S"
START = datetime(2026, 1, 1)
END = datetime(2026, 9, 1)

NAMES = ["Sam Rivera", "Priya Shah", "Jonas Berg", "Mei Chen", "Omar Haddad", "Lena Novak",
         "Kofi Mensah", "Ana Souza", "Tom Becker", "Yuki Sato", "Ivy Brooks", "Raj Patel",
         "Nora Quinn", "Leo Martin"]  # fmt: skip
TEAMS = ["billing", "technical", "onboarding"]
SUBJECTS = [
    "Can't log in", "Invoice is wrong", "Export times out", "Feature request: SSO",
    "Webhook not firing", "Upgrade plan", "Data looks stale", "Password reset email",
]  # fmt: skip
STATUSES = ["open", "pending", "solved", "closed"]
PRIORITIES = ["low", "normal", "high", "urgent"]
TAGS = ["api", "billing", "bug", "enterprise", "churn-risk", "mobile"]
PLANS = ["starter", "growth", "enterprise"]


def fmt(dt: datetime) -> str:
    return dt.strftime(FORMAT)


def _between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    return start + timedelta(seconds=rng.randint(0, int((end - start).total_seconds())))


def _hours(value: float) -> float | int:
    return int(value) if value == int(value) else value


def generate(seed: int = 42, tickets: int = 420) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    agents: list[dict[str, Any]] = []
    for n, name in enumerate(NAMES, start=1):
        created = _between(rng, START, START + timedelta(days=60))
        agents.append(
            {
                "id": n,
                "name": name,
                "email": f"{name.split()[0].lower()}@initech.example",
                "team": TEAMS[n % len(TEAMS)],
                "active": rng.random() > 0.2,
                "max_open_tickets": rng.choice([10, 20, 30]),
                "created_at": fmt(created),
                "modified_at": fmt(_between(rng, created, END).replace(minute=0, second=0)),
            }
        )

    rows: list[dict[str, Any]] = []
    for n in range(1, tickets + 1):
        created = _between(rng, START, END - timedelta(days=1))
        modified = _between(rng, created, END).replace(minute=0, second=0)
        status = rng.choice(STATUSES)
        rows.append(
            {
                "id": 10_000 + n,
                "subject": rng.choice(SUBJECTS),
                "status": status,
                "priority": rng.choice(PRIORITIES),
                "requester": {
                    "name": f"Customer {n}",
                    "email": f"customer{n}@example.com",
                },
                "assignee_id": None if rng.random() < 0.2 else rng.choice(agents)["id"],
                "tags": rng.sample(TAGS, k=rng.randint(0, 2)),
                "satisfaction_score": rng.randint(1, 5) if status in ("solved", "closed") else None,
                "time_spent_hours": _hours(rng.randint(0, 40) / 4),
                "custom_fields": {
                    "plan": rng.choice(PLANS),
                    "region": rng.choice(["us", "eu", "apac", None]),
                },
                "created_at": fmt(created),
                "modified_at": fmt(max(modified, created)),
            }
        )
    return {"tickets": rows, "agents": agents}
