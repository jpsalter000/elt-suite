"""Deterministic seeded dataset for the Cartwheel mock API.

Deliberately unlike Shopfront: camelCase, integer IDs, cents, mixed currencies,
epoch seconds, ``Y``/``N`` flags, ``""`` for missing values, and soft deletes.
``modified`` is truncated to the hour, so many records tie on it; that is what
makes the default (unstable) sort a paging hazard.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

START = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
END = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
HOUR = 3600

FIRST = ["Alex", "Jordan", "Casey", "Riley", "Morgan", "Taylor", "Jamie", "Avery", "Quinn"]
LAST = ["Nguyen", "Garcia", "Smith", "Okafor", "Kowalski", "Rossi", "Tanaka", "Silva"]
PLACES = [
    ("Portland", "United States"),
    ("Vancouver", "Canada"),
    ("Bristol", "United Kingdom"),
    ("Munich", "Germany"),
    ("", ""),
]
TIERS = ["BRONZE", "SILVER", "GOLD", ""]
STATES = ["AWAITING_PAYMENT", "FULFILLED", "IN_TRANSIT", "COMPLETE", "VOIDED", "RETURNED"]
PRODUCTS = {"WIDGET-S": 1250, "WIDGET-L": 2400, "GIZMO": 4999, "CABLE-2M": 899, "MOUNT": 1575}
SHIPPING_CENTS = [0, 499, 1299]


def _hour(ts: int) -> int:
    return ts - ts % HOUR


def _between(rng: random.Random, start: int, end: int) -> int:
    return rng.randint(start, max(start, end))


def generate(seed: int = 42, customers: int = 150, orders: int = 450) -> dict[str, list[dict]]:
    rng = random.Random(seed)

    customer_rows: list[dict[str, Any]] = []
    for n in range(1, customers + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        city, country = rng.choice(PLACES)
        created = _between(rng, START, END - 60 * 86400)
        modified = _hour(_between(rng, created, END))
        deleted = rng.random() < 0.06
        customer_rows.append(
            {
                "customerId": 5000 + n,
                "firstName": first,
                "lastName": last,
                "emailAddress": f"{first}.{last}.{n}@example.net".lower(),
                "loyaltyTier": rng.choice(TIERS),
                "marketingConsent": rng.choice("YN"),
                "addrLine1": f"{rng.randint(1, 400)} Elm Ave" if city else "",
                "addrCity": city,
                "addrCountry": country,
                "created": created,
                "modified": modified,
                "isDeleted": deleted,
                "deletedAt": modified if deleted else None,
            }
        )

    order_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for n in range(1, orders + 1):
        customer = rng.choice(customer_rows)
        placed = _between(rng, customer["created"], END - 86400)
        modified = _hour(_between(rng, placed, END))
        items = []
        for code in rng.sample(sorted(PRODUCTS), k=rng.randint(1, 4)):
            quantity = rng.randint(1, 5)
            items.append(
                {
                    "orderItemId": len(item_rows) + len(items) + 1,
                    "orderId": 900_000 + n,
                    "productCode": code,
                    "quantity": quantity,
                    "unitPriceCents": PRODUCTS[code],
                    "lineTotalCents": quantity * PRODUCTS[code],
                    "modified": modified,
                    "isDeleted": False,
                    "deletedAt": None,
                }
            )
        item_rows += items
        subtotal = sum(i["lineTotalCents"] for i in items)
        promo = rng.choice(["", "", "", "SPRING15"])
        discount = round(subtotal * 0.15) if promo else 0
        shipping = rng.choice(SHIPPING_CENTS)
        deleted = rng.random() < 0.05
        order_rows.append(
            {
                "orderId": 900_000 + n,
                "customerId": customer["customerId"],
                "orderState": rng.choice(STATES),
                "currencyCode": rng.choice(["USD", "USD", "EUR", "GBP"]),
                "subtotalCents": subtotal,
                "shippingCents": shipping,
                "discountCents": discount,
                "totalCents": subtotal + shipping - discount,
                "promoCode": promo,
                "placed": placed,
                "modified": modified,
                "isDeleted": deleted,
                "deletedAt": modified if deleted else None,
            }
        )

    return {"customers": customer_rows, "orders": order_rows, "order_items": item_rows}
