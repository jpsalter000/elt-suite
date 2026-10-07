"""Contract tests for the NetSuite mock API (SuiteQL over REST, Token-Based Authentication).

The mock stands in for one NetSuite account, "Vandelay Industries", a research
consultancy whose timesheets, projects and project transactions feed the
utilization and project-margin reporting:

- ``POST /services/rest/query/v1/suiteql?limit=&offset=`` with ``{"q": "<SuiteQL>"}``
  and the ``Prefer: transient`` header, signed with OAuth 1.0a HMAC-SHA256 (TBA).
- Pages look like NetSuite's: ``{links, count, hasMore, items, offset, totalResults}``;
  every item carries its own ``links`` array.
- ``limit`` is 1-1000, ``offset`` must be a multiple of ``limit`` and stay below
  100,000, as in NetSuite.
- A subset of SuiteQL is understood: ``SELECT`` columns, ``*`` or
  ``TO_CHAR(col, 'fmt') AS alias``; one table; an optional
  ``WHERE col >= TO_DATE('...', 'YYYY-MM-DD HH24:MI:SS')``; ``ORDER BY``.
- Like SuiteQL, every value is a string, booleans are ``"T"``/``"F"``, raw dates use
  the account's date format (``M/D/YYYY``), and null fields are left out of the item.
- Errors are NetSuite-shaped: ``{type, title, status, "o:errorDetails": [{detail,
  "o:errorCode"}]}``.
"""

import re
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from consumers.netsuite.client import TokenAuth
from mock_apis.netsuite import DEMO_CREDENTIALS, create_app
from mock_apis.netsuite.data import COLUMNS, generate

URL = "http://localhost:8005/services/rest/query/v1/suiteql"
PREFER = {"Prefer": "transient"}
AUTH = TokenAuth(DEMO_CREDENTIALS)


@pytest.fixture(scope="module")
def api():
    return TestClient(create_app())


@pytest.fixture(scope="module")
def dataset():
    return generate()


def suiteql(api, q, *, limit=None, offset=None, auth=AUTH, headers=PREFER):
    params = {k: v for k, v in {"limit": limit, "offset": offset}.items() if v is not None}
    return api.post(URL, params=params, json={"q": q}, headers=headers, auth=auth)


def page(api, q, **kwargs):
    resp = suiteql(api, q, **kwargs)
    assert resp.status_code == 200, resp.json()
    return resp.json()


def all_items(api, q, limit=1000):
    items, offset = [], 0
    while True:
        body = page(api, q, limit=limit, offset=offset)
        items += body["items"]
        if not body["hasMore"]:
            return items
        offset += limit


def errors(resp, status):
    assert resp.status_code == status, resp.json()
    body = resp.json()
    assert body["status"] == status and body["title"] and body["type"], body
    details = body["o:errorDetails"]
    assert details and all({"detail", "o:errorCode"} <= d.keys() for d in details), body
    return details


def detail(resp, status):
    return " | ".join(d["detail"] for d in errors(resp, status))


# --- valid queries returning data ---


def test_pages_look_like_netsuite_suiteql_responses(api, dataset):
    body = page(api, "SELECT id, entityid FROM employee ORDER BY id", limit=5)
    assert set(body) == {"links", "count", "hasMore", "items", "offset", "totalResults"}
    assert body["count"] == 5 and body["offset"] == 0 and body["hasMore"] is True
    assert body["totalResults"] == len(dataset["employee"])
    assert {link["rel"] for link in body["links"]} >= {"self", "next"}
    for item in body["items"]:
        assert item["links"] == []
        assert set(item) == {"links", "id", "entityid"}


def test_values_are_strings_booleans_are_t_or_f_and_nulls_are_omitted(api):
    items = all_items(api, "SELECT id, isinactive, releasedate, laborcost FROM employee")
    assert all(isinstance(v, str) for item in items for k, v in item.items() if k != "links")
    assert {item["isinactive"] for item in items} == {"T", "F"}
    released = [item for item in items if "releasedate" in item]
    assert 0 < len(released) < len(items)
    assert all(re.fullmatch(r"\d+(\.\d+)?", item["laborcost"]) for item in items)


def test_raw_dates_use_the_account_format_and_to_char_formats_them(api):
    items = all_items(
        api,
        "SELECT hiredate, TO_CHAR(hiredate, 'YYYY-MM-DD') AS hired, "
        "TO_CHAR(lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS') AS lastmodifieddate FROM employee",
    )
    for item in items:
        assert re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", item["hiredate"])
        hired = date.fromisoformat(item["hired"])
        assert item["hiredate"] == f"{hired.month}/{hired.day}/{hired.year}"
        datetime.strptime(item["lastmodifieddate"], "%Y-%m-%d %H:%M:%S")


def test_select_star_returns_every_populated_column(api):
    (item,) = page(api, "SELECT * FROM department ORDER BY id", limit=1)["items"]
    assert set(item) - {"links"} <= set(COLUMNS["department"])
    assert {"id", "name"} <= set(item)


def test_lower_bound_is_inclusive(api):
    stamps = sorted(
        item["modified"]
        for item in all_items(
            api, "SELECT TO_CHAR(lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS') AS modified FROM job"
        )
    )
    bound = stamps[len(stamps) // 2]
    items = all_items(
        api,
        "SELECT TO_CHAR(lastmodifieddate, 'YYYY-MM-DD HH24:MI:SS') AS modified FROM job "
        f"WHERE lastmodifieddate >= TO_DATE('{bound}', 'YYYY-MM-DD HH24:MI:SS')",
    )
    assert sorted(item["modified"] for item in items) == [s for s in stamps if s >= bound]
    assert bound in {item["modified"] for item in items}


def test_order_by_several_columns_and_descending(api):
    items = all_items(api, "SELECT employee, id FROM timebill ORDER BY employee DESC, id")
    keys = [(int(item["employee"]), int(item["id"])) for item in items]
    assert keys == sorted(keys, key=lambda k: (-k[0], k[1]))


def test_paging_with_limit_and_offset_returns_every_row_once(api, dataset):
    ids = [item["id"] for item in all_items(api, "SELECT id FROM timebill ORDER BY id", limit=500)]
    assert len(ids) == len(set(ids)) == len(dataset["timebill"])
    assert ids == sorted(ids, key=int)


def test_every_table_is_queryable(api, dataset):
    for table in COLUMNS:
        body = page(api, f"SELECT * FROM {table}", limit=1)
        assert body["totalResults"] == len(dataset[table]) > 0, table


def test_every_column_is_populated_somewhere(dataset):
    """Schema inference only sees fields that are non-null at least once."""
    for table, columns in COLUMNS.items():
        for column in columns:
            assert any(row.get(column) is not None for row in dataset[table]), (table, column)


def test_dataset_is_deterministic():
    assert generate(seed=7) == generate(seed=7)
    assert generate(seed=7) != generate(seed=8)


def test_dataset_exercises_the_reporting_rules(dataset):
    """The mock has to contain every case the dbt models handle differently."""
    employees = dataset["employee"]
    window = (date(2026, 1, 1), date(2026, 8, 31))
    assert any(window[0] < e["hiredate"] <= window[1] for e in employees), "mid-window hire"
    assert any(
        e.get("releasedate") and window[0] <= e["releasedate"] < window[1] for e in employees
    )
    assert {e["custentity_hours_per_day"] for e in employees} >= {6, 8}, "part-timers"

    items = {i["id"]: i["itemid"] for i in dataset["item"]}
    entries = dataset["timebill"]
    logged = {items[t["item"]] for t in entries}
    assert {"Holiday", "PTO/Personal Leave/Birthday", "Project Time", "Misc Time"} <= logged
    assert any(t["trandate"].weekday() >= 5 for t in entries), "weekend work"
    pto = [t for t in entries if items[t["item"]] == "PTO/Personal Leave/Birthday"]
    assert any(t["hours"] > 8 for t in pto), "exempt time above the standard day"

    statuses = {j["custentity_project_status"] for j in dataset["job"]}
    assert statuses == {
        "1. Bus Dev/Consulting",
        "2. Awarded",
        "3. On Hold",
        "4. Finalized",
        "5. Closed",
    }
    tx = dataset["transaction"]
    assert {t["type"] for t in tx} >= {
        "SalesOrd", "CustInvc", "CustCred", "PurchOrd", "VendBill", "ExpRept", "VendAuth",
        "Journal",
    }  # fmt: skip
    po_statuses = {t["status"] for t in tx if t["type"] == "PurchOrd"}
    assert po_statuses >= {"Pending Supervisor Approval", "Pending Billing", "Fully Billed"}
    assert "Rejected by Supervisor" in {t["status"] for t in tx if t["type"] == "ExpRept"}
    accounts = {a["id"]: a for a in dataset["account"]}
    used = {accounts[line["account"]]["acctnumber"] for line in dataset["transactionaccountingline"]
            if accounts[line["account"]].get("acctnumber")}  # fmt: skip
    assert used >= {"32000", "40000", "40110", "40120", "50100", "50200", "50300", "50400"}
    deferred = {a["id"] for a in accounts.values() if a["accttype"] == "DeferRevenue"}
    assert any(line["account"] in deferred for line in dataset["transactionaccountingline"])


# --- valid queries returning no data ---


def test_a_lower_bound_after_every_record_returns_an_empty_page(api):
    body = page(
        api,
        "SELECT id FROM timebill "
        "WHERE lastmodifieddate >= TO_DATE('2030-01-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')",
    )
    assert body["items"] == [] and body["count"] == 0
    assert body["hasMore"] is False and body["totalResults"] == 0


def test_an_offset_past_the_last_row_returns_an_empty_page(api, dataset):
    limit = 1000
    offset = (len(dataset["employee"]) // limit + 1) * limit
    body = page(api, "SELECT id FROM employee", limit=limit, offset=offset)
    assert body["items"] == [] and body["hasMore"] is False


# --- invalid queries surface the API's explanation ---


def test_unknown_table(api):
    message = detail(suiteql(api, "SELECT id FROM widgets"), 400)
    assert "Record 'widgets' was not found" in message


def test_unknown_column(api):
    message = detail(suiteql(api, "SELECT id, salary FROM employee"), 400)
    assert "Field 'salary' for record 'employee' was not found" in message


@pytest.mark.parametrize(
    "query",
    [
        "SELECT e.id FROM employee e JOIN department d ON e.department = d.id",
        "SELECT department, COUNT(*) FROM employee GROUP BY department",
        "SELECT id FROM employee WHERE isinactive = 'F'",
        "DELETE FROM employee",
    ],
)
def test_unsupported_suiteql_is_rejected_with_the_supported_grammar(api, query):
    message = detail(suiteql(api, query), 400)
    assert "SELECT" in message and "TO_DATE" in message and "ORDER BY" in message


def test_to_char_needs_a_date_column_and_a_supported_format(api):
    assert "is not a date" in detail(
        suiteql(api, "SELECT TO_CHAR(entityid, 'YYYY-MM-DD') AS x FROM employee"), 400
    )
    assert "YYYY-MM-DD HH24:MI:SS" in detail(
        suiteql(api, "SELECT TO_CHAR(hiredate, 'DD-MON-YY') AS x FROM employee"), 400
    )


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"limit": 1001}, "between 1 and 1000"),
        ({"limit": 0}, "between 1 and 1000"),
        ({"limit": "ten"}, "between 1 and 1000"),
        ({"limit": 100, "offset": 150}, "multiple of the limit"),
        ({"limit": 1000, "offset": 100_000}, "100000"),
        ({"page": 2}, "Invalid query parameter: page"),
    ],
)
def test_paging_parameters_are_validated(api, params, expected):
    resp = api.post(URL, params=params, json={"q": "SELECT id FROM employee"}, headers=PREFER,
                    auth=AUTH)  # fmt: skip
    assert expected in detail(resp, 400)


def test_every_invalid_parameter_is_reported_at_once(api):
    resp = api.post(
        URL, params={"limit": 5000, "page": 1}, json={"q": "SELECT id FROM employee"},
        headers=PREFER, auth=AUTH,
    )  # fmt: skip
    assert len(errors(resp, 400)) == 2


def test_the_prefer_transient_header_is_required(api):
    assert "Prefer: transient" in detail(suiteql(api, "SELECT id FROM employee", headers={}), 400)


def test_the_body_must_carry_a_query(api):
    resp = api.post(URL, json={"query": "SELECT id FROM employee"}, headers=PREFER, auth=AUTH)
    assert '"q"' in detail(resp, 400)


def _login_failure(resp):
    (err,) = errors(resp, 401)
    assert err["o:errorCode"] == "INVALID_LOGIN_ATTEMPT"
    assert "Invalid login attempt" in err["detail"]
    return err["detail"]


def test_requests_must_be_signed(api):
    assert "Authorization" in _login_failure(suiteql(api, "SELECT id FROM employee", auth=None))


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"token_secret": "wrong"}, "signature"),
        ({"consumer_key": "unknown-key"}, "consumer key"),
        ({"token_id": "unknown-token"}, "token"),
        ({"account_id": "OTHER_ACCOUNT"}, "realm"),
    ],
)
def test_bad_credentials_are_rejected(api, override, reason):
    auth = TokenAuth({**DEMO_CREDENTIALS, **override})
    assert reason in _login_failure(suiteql(api, "SELECT id FROM employee", auth=auth))


def test_replayed_nonces_are_rejected(api):
    import httpx

    request = httpx.Request("POST", URL, params={"limit": 1}, json={"q": "SELECT id FROM employee"},
                            headers=PREFER)  # fmt: skip
    signed = next(AUTH.auth_flow(request))
    headers = {**PREFER, "Authorization": signed.headers["Authorization"]}
    first = api.post(URL, params={"limit": 1}, json={"q": "SELECT id FROM employee"},
                     headers=headers)  # fmt: skip
    assert first.status_code == 200
    replay = api.post(URL, params={"limit": 1}, json={"q": "SELECT id FROM employee"},
                      headers=headers)  # fmt: skip
    assert "nonce" in _login_failure(replay)


def test_stale_timestamps_are_rejected():
    import time

    an_hour_ahead = TestClient(create_app(clock=lambda: time.time() + 3600))
    assert "timestamp" in _login_failure(suiteql(an_hour_ahead, "SELECT id FROM employee"))


def test_unknown_paths_and_methods_are_netsuite_errors(api):
    assert "/services/rest/query/v1/suiteql" in detail(api.get("/services/rest/record/v1/x"), 404)
    errors(api.get(URL, headers=PREFER), 405)
