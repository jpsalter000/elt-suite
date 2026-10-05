"""Deterministic seeded dataset for the NetSuite mock: one account, Vandelay Industries.

Vandelay is a market-research consultancy. Its staff log time against client
projects and internal work, and its projects carry sales orders, invoices, vendor
bills, purchase orders and expense reports. Together they feed the utilization
and project-margin reporting.

Records keep native Python types (``int`` ids, ``date``/``datetime``, ``float``
hours and amounts, ``bool`` flags, ``None`` for null). The app renders them the
way SuiteQL does. ``COLUMNS`` declares every table's columns and their types.

The data deliberately covers every case the transformations treat differently:

- **Staffing.** Mid-window hires and releases, one release before the window,
  part-timers on 6-hour days, and varied cost rates.
- **Time.** Company holidays, full and half PTO days, a PTO day longer than the
  standard day, rare leave types, weekend work, and one item ("Misc Time") that
  the category map deliberately omits.
- **Projects.** Every project status, from "1. Bus Dev/Consulting" to
  "5. Closed".
- **Transactions.**
  - Sales orders, invoices and credit memos, with pass-through travel and
    incentive revenue lines.
  - Vendor bills, and purchase orders in every status.
  - Expense reports, including rejected ones.
  - Vendor returns, opening-balance journals, and deferred-revenue journals.
  - Amounts on ``transactionaccountingline`` follow NetSuite's sign convention:
    debits are positive and credits negative, so income on an invoice is negative.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

START = date(2026, 1, 1)
END = date(2026, 8, 31)  # last day of logged time (inclusive)
LAST_MODIFIED = datetime(2026, 9, 1, tzinfo=UTC)  # nothing is modified after this

# Column types: int, number, text, bool, date, datetime.
COLUMNS: dict[str, dict[str, str]] = {
    "department": {"id": "int", "name": "text", "isinactive": "bool"},
    "employee": {
        "id": "int", "entityid": "text", "firstname": "text", "lastname": "text",
        "email": "text", "department": "int", "title": "text", "isinactive": "bool",
        "hiredate": "date", "releasedate": "date", "laborcost": "number",
        "custentity_burdened_cost": "number", "custentity_hours_per_day": "number",
        "lastmodifieddate": "datetime",
    },
    "customer": {
        "id": "int", "entityid": "text", "companyname": "text", "email": "text",
        "phone": "text", "isinactive": "bool", "subsidiary": "int",
        "datecreated": "datetime", "lastmodifieddate": "datetime",
    },
    "job": {
        "id": "int", "entityid": "text", "companyname": "text", "parent": "int",
        "custentity_project_status": "text", "startdate": "date",
        "projectedenddate": "date", "department": "int", "projectmanager": "int",
        "isinactive": "bool", "lastmodifieddate": "datetime",
    },
    "item": {
        "id": "int", "itemid": "text", "displayname": "text", "itemtype": "text",
        "isinactive": "bool",
    },
    "timebill": {
        "id": "int", "employee": "int", "trandate": "date", "hours": "number",
        "customer": "int", "item": "int", "department": "int", "memo": "text",
        "isbillable": "bool", "lastmodifieddate": "datetime",
    },
    "transaction": {
        "id": "int", "tranid": "text", "type": "text", "status": "text", "entity": "int",
        "currency": "int", "foreigntotal": "number", "memo": "text", "trandate": "date",
        "lastmodifieddate": "datetime",
    },
    "transactionline": {
        "transaction": "int", "id": "int", "linesequencenumber": "int", "mainline": "bool",
        "entity": "int", "item": "int", "memo": "text", "foreignamount": "number",
    },
    "transactionaccountingline": {
        "transaction": "int", "transactionline": "int", "account": "int",
        "amount": "number", "posting": "bool",
    },
    "account": {
        "id": "int", "acctnumber": "text", "fullname": "text", "accttype": "text",
        "isinactive": "bool",
    },
}  # fmt: skip

DEPARTMENTS = [
    (1, "Consumer Group"),
    (2, "Life Sciences"),
    (3, "Technology Group"),
    (4, "Operations"),
    (5, "Special Services"),
    (6, "Admin"),
]
CLIENT_DEPARTMENTS = [1, 2, 3]
STAFF_PER_DEPARTMENT = {1: 10, 2: 9, 3: 8, 4: 5, 5: 4, 6: 4}
TITLES = {
    "client": ["Research Director", "Senior Project Manager", "Project Manager",
               "Senior Analyst", "Analyst", "Analyst"],
    "support": ["Operations Manager", "Coordinator", "Specialist", "Specialist"],
}  # fmt: skip
BASE_RATE = {
    "Research Director": 110.0, "Senior Project Manager": 85.0, "Project Manager": 70.0,
    "Senior Analyst": 60.0, "Analyst": 45.0, "Operations Manager": 65.0,
    "Coordinator": 38.0, "Specialist": 42.0,
}  # fmt: skip

FIRST = ["Ava", "Ben", "Cleo", "Dev", "Esme", "Finn", "Gia", "Hugo", "Ines", "Jon", "Kira",
         "Leo", "Mina", "Nate", "Opal", "Paz", "Quinn", "Ravi", "Sana", "Theo", "Uma",
         "Vic", "Wren", "Xavi", "Yara", "Zeke"]  # fmt: skip
LAST = ["Abara", "Brandt", "Castell", "Dorsey", "Ekwueme", "Farrow", "Galvan", "Hollis",
        "Imai", "Jaskar", "Kovac", "Lindqvist", "Marsh", "Nakamura", "Okafor", "Pryce",
        "Quint", "Roshan", "Sato", "Tamsin", "Ueda", "Varga", "Whitlow", "Yilmaz"]  # fmt: skip

CUSTOMERS = [
    "Northwind Foods", "Bluepeak Pharma", "Corvid Analytics", "Delmar Beverages",
    "Everline Health", "Fable & Finch Retail", "Granite Mobile", "Halcyon Biologics",
    "Ironleaf Software", "Juniper Home Goods", "Kestrel Devices", "Lumen Diagnostics",
    "Meridian Snacks", "Nimbus Cloudworks", "Orchid Therapeutics", "Pinecrest Apparel",
    "Quarry Robotics", "Riverside Dairy", "Solace Medical", "Tidewater Gaming",
]  # fmt: skip
INTERNAL_CUSTOMER = "Vandelay Industries (Internal)"
STUDIES = {
    1: ["Brand Tracker", "Shopper Journey Study", "Pack Test", "Concept Screen",
        "Segmentation", "Ad Effectiveness"],
    2: ["KOL Interviews", "Payer Advisory Board", "Patient Journey", "Message Testing",
        "Launch Tracker", "Clinical Landscape"],
    3: ["Usability Benchmark", "Pricing Study", "Market Sizing", "Win/Loss Interviews",
        "Feature Prioritization", "Customer Satisfaction"],
}  # fmt: skip
PROJECT_STATUSES = [
    ("1. Bus Dev/Consulting", 6),
    ("2. Awarded", 14),
    ("3. On Hold", 5),
    ("4. Finalized", 15),
    ("5. Closed", 18),
]

# (itemid, category) - the category lives in the dbt seed, not in NetSuite; it is
# kept here only to drive realistic time allocation.
TIME_ITEMS = [
    ("Project Time", "project"),
    ("Business Development", "bd"),
    ("RFP Response/Proposal Development", "bd"),
    ("Marketing/Design", "bd"),
    ("Client and/or Team Strategy/Planning/Meeting", "internal"),
    ("Training", "internal"),
    ("Staffing/Resource Management", "internal"),
    ("Continuing Education/Professional Development", "internal"),
    ("Innovation/Methods Development", "internal"),
    ("Admin", "admin"),
    ("Meeting", "admin"),
    ("Travel (Business Development)", "admin"),
    ("Travel (Corporate)", "admin"),
    ("Holiday", "exempt"),
    ("PTO/Personal Leave/Birthday", "exempt"),
    ("Volunteer", "exempt"),
    ("Jury Duty", "exempt"),
    ("Bereavement", "exempt"),
    ("FMLA", "exempt"),
    ("Misc Time", "unmapped"),
]
SALES_ITEM = "Research Services"
RETIRED_ITEM = "Legacy Utilized Time"
SALES_ITEM_ID = 5 + len(TIME_ITEMS)  # item ids are 5 + position in _items()
CATEGORY_WEIGHTS = {
    "client": {"project": 70, "bd": 8, "internal": 10, "admin": 12},
    "support": {"project": 15, "bd": 3, "internal": 30, "admin": 52},
}
HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25),
    date(2026, 7, 3),
}  # fmt: skip

# (id, acctnumber, fullname, accttype, isinactive)
ACCOUNTS = [
    (101, "10000", "Operating Cash", "Bank", False),
    (102, "11000", "Accounts Receivable", "AcctRec", False),
    (103, "20000", "Accounts Payable", "AcctPay", False),
    (104, None, "Deferred Revenue", "DeferRevenue", False),
    (105, "32000", "Opening Balance", "Equity", False),
    (106, "40000", "Research Revenue", "Income", False),
    (107, "40110", "Pass-through Travel Revenue", "Income", False),
    (108, "40120", "Pass-through Incentive Revenue", "Income", False),
    (109, "50100", "COG Research Services", "COGS", False),
    (110, "50200", "COG Field Services", "COGS", False),
    (111, "50300", "COG Incentives", "COGS", False),
    (112, "50400", "COG Travel", "COGS", False),
    (113, "69999", "Retired Clearing", "Expense", True),
]
ACCOUNT = {number or name: acct_id for acct_id, number, name, _, _ in ACCOUNTS}
AR, AP = ACCOUNT["11000"], ACCOUNT["20000"]
SALES_DOCS = {"SalesOrd", "CustInvc", "CustCred", "Opprtnty"}
LINE_SIGN = {"SalesOrd": -1, "CustInvc": -1, "Opprtnty": -1, "VendAuth": -1,
             "CustCred": 1, "VendBill": 1, "PurchOrd": 1, "ExpRept": 1, "Journal": 1}  # fmt: skip


def _between(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max(0, (end - start).days)))


def _stamp(rng: random.Random, day: date, max_lag_days: int = 5) -> datetime:
    """A modification time on or a few days after ``day``, never after LAST_MODIFIED."""
    moment = datetime.combine(day, time(8), UTC) + timedelta(
        days=rng.randint(0, max_lag_days), minutes=rng.randint(0, 10 * 60)
    )
    return min(moment, LAST_MODIFIED - timedelta(seconds=rng.randint(1, 3600)))


def _quarters(rng: random.Random, total: float, parts: int) -> list[float]:
    """Split ``total`` hours into ``parts`` positive quarter-hour amounts."""
    quarters = int(round(total * 4))
    parts = max(1, min(parts, quarters))
    cuts = sorted(rng.sample(range(1, quarters), parts - 1)) if parts > 1 else []
    bounds = [0, *cuts, quarters]
    return [(b - a) / 4 for a, b in zip(bounds, bounds[1:], strict=False)]


def _departments() -> list[dict[str, Any]]:
    rows = [{"id": i, "name": name, "isinactive": False} for i, name in DEPARTMENTS]
    rows.append({"id": 7, "name": "Field Operations (retired)", "isinactive": True})
    return rows


def _employees(rng: random.Random) -> list[dict[str, Any]]:
    names = rng.sample(
        [(f, last) for f in FIRST for last in LAST], sum(STAFF_PER_DEPARTMENT.values())
    )
    hires = iter([date(2026, 3, 2), date(2026, 5, 18), date(2026, 6, 15), date(2026, 8, 3)])
    releases = iter([date(2025, 11, 14), date(2026, 2, 13), date(2026, 4, 30), date(2026, 6, 5),
                     date(2026, 7, 17), date(2026, 8, 21)])  # fmt: skip
    rows: list[dict[str, Any]] = []
    for dept, count in STAFF_PER_DEPARTMENT.items():
        kind = "client" if dept in CLIENT_DEPARTMENTS else "support"
        for n in range(count):
            first, last = names[len(rows)]
            title = TITLES[kind][n % len(TITLES[kind])]
            rate = BASE_RATE[title] + rng.choice([-5, -2.5, 0, 0, 2.5, 5, 7.5])
            index = len(rows)
            hired = _between(rng, date(2017, 1, 9), date(2025, 10, 31))
            released = None
            if index % 7 == 3:
                hired = next(hires, hired)
            elif index % 7 == 5:
                released = next(releases, None)
            rows.append(
                {
                    "id": 2100 + index * 3,
                    "entityid": f"{first} {last}",
                    "firstname": first,
                    "lastname": last,
                    "email": f"{first.lower()}.{last.lower()}@vandelay.example",
                    "department": dept,
                    "title": title,
                    "isinactive": released is not None and released < date(2026, 8, 1),
                    "hiredate": hired,
                    "releasedate": released,
                    "laborcost": rate,
                    "custentity_burdened_cost": round(rate * rng.choice([1.25, 1.3, 1.35, 1.4]), 2),
                    "custentity_hours_per_day": 6 if index in (8, 27) else 8,
                    "lastmodifieddate": _stamp(rng, max(hired, START), 60),
                }
            )
    return rows


def _customers(rng: random.Random) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n, name in enumerate([*CUSTOMERS, INTERNAL_CUSTOMER]):
        created = datetime(2019, 1, 1, tzinfo=UTC) + timedelta(days=rng.randint(0, 2400))
        slug = name.split()[0].lower().replace("&", "and")
        rows.append(
            {
                "id": 801 + n * 2,
                "entityid": f"C{1001 + n}",
                "companyname": name,
                "email": None if n % 4 == 3 else f"research@{slug}.example",
                "phone": None if n % 3 == 2 else f"555-01{n:02d}",
                "isinactive": n == 17,
                "subsidiary": 1,
                "datecreated": created,
                "lastmodifieddate": _stamp(rng, _between(rng, START, END), 3),
            }
        )
    return rows


def _projects(rng, customers, employees) -> list[dict[str, Any]]:
    clients = [c for c in customers if c["companyname"] != INTERNAL_CUSTOMER]
    statuses = [s for s, count in PROJECT_STATUSES for _ in range(count)]
    rng.shuffle(statuses)
    rows: list[dict[str, Any]] = []
    for n, status in enumerate(statuses):
        dept = CLIENT_DEPARTMENTS[n % len(CLIENT_DEPARTMENTS)]
        customer = rng.choice(clients)
        managers = [e for e in employees if e["department"] == dept and "Manager" in e["title"]]
        if status in ("4. Finalized", "5. Closed"):
            start = _between(rng, date(2025, 10, 1), date(2026, 5, 15))
        else:
            start = _between(rng, date(2026, 1, 5), date(2026, 7, 31))
        rows.append(
            {
                "id": 100051 + n * 7,
                "entityid": str(100051 + n * 7),
                "companyname": f"{rng.choice(STUDIES[dept])} {2026 if n % 3 else 2025}"
                + (f" Wave {n % 4 + 1}" if n % 2 else ""),
                "parent": customer["id"],
                "custentity_project_status": status,
                "startdate": start,
                "projectedenddate": None
                if n % 9 == 4
                else start + timedelta(days=rng.randint(30, 150)),
                "department": dept,
                "projectmanager": rng.choice(managers)["id"],
                "isinactive": status == "5. Closed" and n % 3 == 0,
                "lastmodifieddate": _stamp(rng, max(start, START), 30),
            }
        )
    return rows


def _items() -> list[dict[str, Any]]:
    names = [*(name for name, _ in TIME_ITEMS), SALES_ITEM, RETIRED_ITEM]
    return [
        {
            "id": 5 + n,
            "itemid": name,
            "displayname": None if name == "Misc Time" else name,
            "itemtype": "Service",
            "isinactive": name == RETIRED_ITEM,
        }
        for n, name in enumerate(names)
    ]


def _employed(employee: dict[str, Any], day: date) -> bool:
    released = employee["releasedate"]
    return employee["hiredate"] <= day and (released is None or day <= released)


def _timebills(rng, employees, customers, projects, items) -> list[dict[str, Any]]:
    item_id = {i["itemid"]: i["id"] for i in items}
    by_category: dict[str, list[str]] = {}
    for name, category in TIME_ITEMS:
        by_category.setdefault(category, []).append(name)
    internal = next(c["id"] for c in customers if c["companyname"] == INTERNAL_CUSTOMER)
    clients = [c["id"] for c in customers if c["id"] != internal]
    active = [p for p in projects if p["custentity_project_status"] != "1. Bus Dev/Consulting"]

    rows: list[dict[str, Any]] = []
    tenth_hour_pto = False

    def add(emp, day, item, hours, customer=None, memo=None):
        rows.append(
            {
                "id": 300001 + len(rows),
                "employee": emp["id"],
                "trandate": day,
                "hours": hours,
                "customer": customer,
                "item": item_id[item],
                "department": emp["department"],
                "memo": memo,
                "isbillable": item == "Project Time",
                "lastmodifieddate": _stamp(rng, day),
            }
        )

    def work(emp, day, total, kind):
        weights = CATEGORY_WEIGHTS[kind]
        for hours in _quarters(rng, total, rng.randint(1, 4)):
            category = rng.choices(list(weights), list(weights.values()))[0]
            item = rng.choice(by_category[category])
            if rng.random() < 0.004:
                item = "Misc Time"
            if item == "Project Time":
                mine = [p for p in active if p["department"] == emp["department"]
                        and p["startdate"] <= day] or active  # fmt: skip
                add(emp, day, item, hours, rng.choice(mine)["id"])
            elif category == "bd":
                add(emp, day, item, hours, rng.choice(clients))
            else:
                customer = internal if rng.random() < 0.5 else None
                add(emp, day, item, hours, customer, "Weekly sync" if item == "Meeting" else None)

    for emp in employees:
        kind = "client" if emp["department"] in CLIENT_DEPARTMENTS else "support"
        standard = emp["custentity_hours_per_day"]
        day = START
        while day <= END:
            if not _employed(emp, day):
                day += timedelta(days=1)
                continue
            if day.weekday() >= 5:
                if rng.random() < 0.03:
                    add(emp, day, "Project Time", rng.choice([1.5, 2.0, 3.0, 4.0]),
                        rng.choice(active)["id"])  # fmt: skip
            elif day in HOLIDAYS:
                add(emp, day, "Holiday", standard)
            else:
                roll = rng.random()
                if roll < 0.05:
                    hours = standard
                    if not tenth_hour_pto and standard == 8:
                        hours, tenth_hour_pto = 10.0, True  # longer than the standard day
                    add(emp, day, "PTO/Personal Leave/Birthday", hours)
                elif roll < 0.07:
                    add(emp, day, "PTO/Personal Leave/Birthday", standard / 2)
                    work(emp, day, standard / 2, kind)
                elif roll < 0.075:
                    leave = rng.choice(["Volunteer", "Jury Duty", "Bereavement", "FMLA"])
                    add(emp, day, leave, standard)
                else:
                    total = standard + rng.choice([-1.5, -1, -0.5, 0, 0, 0, 0.5, 1, 1.5, 2])
                    work(emp, day, total, kind)
            day += timedelta(days=1)
    return rows


class _Ledger:
    """Builds transactions with their lines and accounting lines."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.transactions: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []
        self.accounting: list[dict[str, Any]] = []
        self.numbers: dict[str, int] = {}

    def add(self, kind, status, entity, project, day, lines, *, memo=None, posting=True):
        """``lines`` are (account key, document amount); signs follow the document."""
        prefix = {"SalesOrd": "SO", "CustInvc": "INV", "CustCred": "CM", "PurchOrd": "PO",
                  "VendBill": "BILL", "ExpRept": "EXP", "VendAuth": "VRA", "Journal": "JE",
                  "Opprtnty": "OPP"}[kind]  # fmt: skip
        self.numbers[prefix] = self.numbers.get(prefix, 1000) + 1
        tx_id = 70001 + len(self.transactions) * 3
        total = round(sum(amount for _, amount in lines), 2)
        self.transactions.append(
            {
                "id": tx_id,
                "tranid": f"{prefix}{self.numbers[prefix]}",
                "type": kind,
                "status": status,
                "entity": entity,
                "currency": 1,
                "foreigntotal": total,
                "memo": memo,
                "trandate": day,
                "lastmodifieddate": _stamp(self.rng, day, 10),
            }
        )
        # Accounting amounts are debits (+) and credits (-). Sales documents credit
        # income and debit receivables; credit memos reverse that. Purchases debit
        # costs and credit payables; vendor returns reverse that.
        sign = LINE_SIGN[kind]
        main_account = {"Journal": ACCOUNT["10000"]}.get(kind, AR if kind in SALES_DOCS else AP)
        self._line(tx_id, 0, True, entity, None, None, total)
        self._accounting(tx_id, 0, main_account, -sign * total, posting)
        for n, (account, amount) in enumerate(lines, start=1):
            item = SALES_ITEM_ID if kind in SALES_DOCS else None
            self._line(tx_id, n, False, project, item, memo if n == 1 else None, amount)
            self._accounting(tx_id, n, ACCOUNT[account], sign * amount, posting)

    def _line(self, tx, line, mainline, entity, item, memo, amount):
        self.lines.append(
            {
                "transaction": tx,
                "id": line,
                "linesequencenumber": line,
                "mainline": mainline,
                "entity": entity,
                "item": item,
                "memo": memo,
                "foreignamount": round(amount, 2),
            }
        )

    def _accounting(self, tx, line, account, amount, posting):
        self.accounting.append(
            {
                "transaction": tx,
                "transactionline": line,
                "account": account,
                "amount": round(amount, 2),
                "posting": posting,
            }
        )


def _money(rng: random.Random, low: int, high: int) -> float:
    return float(rng.randrange(low, high, 50))


def _transactions(rng, projects) -> _Ledger:
    ledger = _Ledger(rng)
    vendors = list(range(9001, 9013))
    for p in projects:
        status, day = p["custentity_project_status"], p["startdate"]
        customer, pid = p["parent"], p["id"]
        later = min(day + timedelta(days=rng.randint(20, 90)), END)
        if status == "1. Bus Dev/Consulting":
            ledger.add("Opprtnty", "In Progress", customer, pid, day,
                       [("40000", _money(rng, 15000, 90000))], posting=False)  # fmt: skip
            continue

        base = _money(rng, 20000, 240000)
        travel = _money(rng, 500, 8000) if rng.random() < 0.6 else 0.0
        incentive = _money(rng, 1000, 15000) if rng.random() < 0.7 else 0.0
        order = [("40000", base)]
        if travel:
            order.append(("40110", travel))
        if incentive:
            order.append(("40120", incentive))
        ledger.add("SalesOrd", "Pending Billing" if status != "5. Closed" else "Billed",
                   customer, pid, day, order, memo="Signed proposal", posting=False)  # fmt: skip

        if status in ("4. Finalized", "5. Closed"):
            first_half = round(base * 0.5, 2)
            ledger.add("CustInvc", "Paid In Full", customer, pid, day + timedelta(days=14),
                       [("40000", first_half)])  # fmt: skip
            ledger.add("CustInvc", "Open" if status == "4. Finalized" else "Paid In Full",
                       customer, pid, later, [("40000", base - first_half)]
                       + [(a, v) for a, v in order[1:]])  # fmt: skip
            if rng.random() < 0.3:
                ledger.add("CustCred", "Fully Applied", customer, pid, later,
                           [("40000", _money(rng, 500, 5000))], memo="Scope reduction")  # fmt: skip
            if rng.random() < 0.4:
                ledger.add("Journal", "Approved for Posting", customer, pid, later,
                           [("Deferred Revenue", round(base * 0.1, 2))],
                           memo="Release deferred revenue")  # fmt: skip

        vendor = rng.choice(vendors)
        field = _money(rng, 2000, 40000)
        ledger.add("VendBill", "Paid In Full", vendor, pid, later,
                   [("50200", field), ("50100", _money(rng, 500, 6000))])  # fmt: skip
        if incentive:
            ledger.add("VendBill", "Open", vendor, pid, later,
                       [("50300", round(incentive * rng.choice([0.8, 0.9, 1.0]), 2))])  # fmt: skip
        if travel:
            ledger.add("ExpRept", rng.choice(["Approved by Accounting", "Paid In Full"]),
                       p["projectmanager"], pid, later,
                       [("50400", round(travel * rng.choice([0.7, 0.85, 1.0]), 2))])  # fmt: skip
        ledger.add("PurchOrd", rng.choice(["Pending Supervisor Approval", "Pending Billing",
                                           "Fully Billed", "Pending Receipt"]),
                   vendor, pid, day + timedelta(days=7), [("50200", _money(rng, 1000, 12000))],
                   posting=False)  # fmt: skip
        if rng.random() < 0.25:
            ledger.add("ExpRept", "Rejected by Supervisor", p["projectmanager"], pid, later,
                       [("50400", _money(rng, 200, 2500))], memo="Missing receipts")  # fmt: skip
        if rng.random() < 0.15:
            ledger.add("VendAuth", "Pending Return", vendor, pid, later,
                       [("50200", _money(rng, 200, 2000))], posting=False)  # fmt: skip
        if rng.random() < 0.1:
            ledger.add("Journal", "Approved for Posting", customer, pid, day,
                       [("32000", _money(rng, 1000, 10000))], memo="Opening balance")  # fmt: skip
            deferral = [("Deferred Revenue", _money(rng, 1000, 5000))]
            ledger.add("Journal", "Approved for Posting", customer, pid, later, deferral,
                       memo="Defer revenue")  # fmt: skip
    return ledger


def generate(seed: int = 42) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    departments = _departments()
    employees = _employees(rng)
    customers = _customers(rng)
    projects = _projects(rng, customers, employees)
    items = _items()
    timebills = _timebills(rng, employees, customers, projects, items)
    ledger = _transactions(rng, projects)
    accounts = [
        {"id": i, "acctnumber": number, "fullname": name, "accttype": kind, "isinactive": inactive}
        for i, number, name, kind, inactive in ACCOUNTS
    ]
    return {
        "department": departments,
        "employee": employees,
        "customer": customers,
        "job": projects,
        "item": items,
        "timebill": timebills,
        "transaction": ledger.transactions,
        "transactionline": ledger.lines,
        "transactionaccountingline": ledger.accounting,
        "account": accounts,
    }
