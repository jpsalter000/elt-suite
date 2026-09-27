"""Salesforce REST API client.

Auth: OAuth 2.0 client-credentials flow against a Connected App
(credentials ``client_id`` / ``client_secret``). Records are pulled with SOQL via
``/services/data/<version>/query``, following ``nextRecordsUrl`` until exhausted.
The queried fields come from the sObject's describe metadata, so every
non-compound field is extracted without maintaining field lists by hand.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from elt_suite.contract import JobContext, Record

DEFAULT_API_VERSION = "v61.0"
COMPOUND_TYPES = {"address", "location"}  # components are queried individually


def _authenticate(ctx: JobContext) -> tuple[str, dict[str, str]]:
    resp = ctx.http.post(
        f"{ctx.base_url}/services/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": ctx.credentials["client_id"],
            "client_secret": ctx.credentials["client_secret"],
        },
    )
    resp.raise_for_status()
    token = resp.json()
    return token["instance_url"], {"Authorization": f"Bearer {token['access_token']}"}


def _soql_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def query_sobject(ctx: JobContext, sobject: str) -> Iterator[Record]:
    """Stream every record of ``sobject``, filtered by the job's incremental bound."""
    version = ctx.consumer.extra.get("api_version", DEFAULT_API_VERSION)
    instance_url, headers = _authenticate(ctx)
    api = f"{instance_url}/services/data/{version}"

    describe = ctx.http.get(f"{api}/sobjects/{sobject}/describe", headers=headers)
    describe.raise_for_status()
    fields = [f["name"] for f in describe.json()["fields"] if f["type"] not in COMPOUND_TYPES]

    soql = f"SELECT {', '.join(fields)} FROM {sobject}"
    inc = ctx.job.incremental_loading
    if inc and ctx.lower_bound:
        key = inc.incremental_key
        soql += f" WHERE {key} >= {_soql_datetime(ctx.lower_bound)} ORDER BY {key}"

    batch_size = ctx.consumer.extra.get("page_size", 2000)
    headers = {**headers, "Sforce-Query-Options": f"batchSize={batch_size}"}
    resp = ctx.http.get(f"{api}/query", params={"q": soql}, headers=headers)
    while True:
        resp.raise_for_status()
        page = resp.json()
        for record in page["records"]:
            record.pop("attributes", None)
            yield record
        if page.get("done", True):
            return
        resp = ctx.http.get(f"{instance_url}{page['nextRecordsUrl']}", headers=headers)


def fetch_contacts(ctx: JobContext) -> Iterator[Record]:
    return query_sobject(ctx, "Contact")


def fetch_accounts(ctx: JobContext) -> Iterator[Record]:
    return query_sobject(ctx, "Account")


def fetch_opportunities(ctx: JobContext) -> Iterator[Record]:
    return query_sobject(ctx, "Opportunity")
