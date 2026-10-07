"""Mock REST APIs used as no-credentials demo sources for elt-suite.

Each API is a small FastAPI app with its own authentication and pagination
style, deterministic seeded data, and structured, actionable error messages:

- :mod:`mock_apis.shopfront`: OAuth2 client credentials with rotating refresh
  tokens, keyset cursor pagination.
- :mod:`mock_apis.ticketdesk`: API-key header, page-number pagination with
  ``Link`` / ``X-Total-Count`` headers.
- :mod:`mock_apis.cartwheel`: HTTP Basic, offset/limit pagination, RFC 7807 errors.
- :mod:`mock_apis.helpline`: HMAC-signed requests, a changes feed with late arrivals.
- :mod:`mock_apis.netsuite`: SuiteQL over REST with token-based auth (OAuth 1.0a),
  for the utilization and project-margin reporting.

Run them locally with ``uv run elt-mock <name>``.
"""
