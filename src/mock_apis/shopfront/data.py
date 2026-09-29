"""Deterministic seeded dataset for the Shopfront mock API."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 9, 1, tzinfo=UTC)

FIRST = ["Ada", "Grace", "Alan", "Edsger", "Barbara", "Ken", "Margaret", "Linus", "Radia", "Tim"]
LAST = ["Lovelace", "Hopper", "Turing", "Dijkstra", "Liskov", "Thompson", "Hamilton", "Perlman"]
CITIES = [("Austin", "US"), ("Toronto", "CA"), ("Leeds", "GB"), ("Berlin", "DE"), ("Lyon", "FR")]
TAGS = ["vip", "wholesale", "newsletter", "returning", "b2b", "gift-buyer"]
SKUS = {"TEE-BLK-M": 24.0, "MUG-11OZ": 12.5, "HOODIE-GRY-L": 55.0, "STICKER-PK": 4.99, "CAP": 19.0}
STATUSES = ["pending", "paid", "shipped", "delivered", "refunded", "cancelled"]
SHIPPING = {"standard": 5, "express": 14.5, "pickup": 0}


def timestamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(value: float) -> float | int:
    """Round to cents, emitting whole amounts as integers (as many real APIs do)."""
    value = round(value, 2)
    return int(value) if value == int(value) else value


def _between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    return start + timedelta(seconds=rng.randint(0, int((end - start).total_seconds())))


def generate(seed: int = 42, customers: int = 180, orders: int = 540) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    customer_rows: list[dict[str, Any]] = []
    for n in range(1, customers + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        created = _between(rng, START, END - timedelta(days=30))
        city, country = rng.choice(CITIES)
        customer_rows.append(
            {
                "id": f"cus_{n:04d}",
                "email": f"{first}.{last}{n}@example.com".lower(),
                "name": f"{first} {last}",
                "tier": rng.choice(["free", "pro", "enterprise", None]),
                "marketing_opt_in": rng.random() < 0.4,
                "lifetime_value": 0,  # filled from orders below
                "address": None
                if rng.random() < 0.15
                else {
                    "line1": f"{rng.randint(1, 999)} Main St",
                    "city": city,
                    "country": country,
                    "postal_code": f"{rng.randint(10000, 99999)}",
                },
                "tags": rng.sample(TAGS, k=rng.randint(0, 3)),
                "created_at": timestamp(created),
                "updated_at": timestamp(_between(rng, created, END)),
            }
        )

    order_rows: list[dict[str, Any]] = []
    for n in range(1, orders + 1):
        customer = rng.choice(customer_rows)
        placed = _between(rng, datetime.fromisoformat(customer["created_at"]), END)
        items = [
            {"sku": sku, "quantity": rng.randint(1, 4), "unit_price": SKUS[sku]}
            for sku in rng.sample(sorted(SKUS), k=rng.randint(1, 3))
        ]
        method = rng.choice(sorted(SHIPPING))
        discount = rng.choice([None, None, None, "WELCOME10", "FALL25"])
        subtotal = sum(i["quantity"] * i["unit_price"] for i in items)
        if discount:
            subtotal *= 0.9 if discount == "WELCOME10" else 0.75
        total = _money(subtotal + SHIPPING[method])
        status = rng.choice(STATUSES)
        order_rows.append(
            {
                "id": f"ord_{n:05d}",
                "customer_id": customer["id"],
                "status": status,
                "currency": "USD",
                "total": total,
                "discount_code": discount,
                "line_items": items,
                "shipping": {"method": method, "cost": SHIPPING[method]},
                "placed_at": timestamp(placed),
                "updated_at": timestamp(_between(rng, placed, END)),
            }
        )
        if status not in ("refunded", "cancelled"):
            customer["lifetime_value"] = _money(customer["lifetime_value"] + total)

    return {"customers": customer_rows, "orders": order_rows}
