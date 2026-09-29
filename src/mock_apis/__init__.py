"""Mock REST APIs used as no-credentials demo sources for elt-suite.

Each API is a small FastAPI app with its own authentication and pagination
style, deterministic seeded data, and structured, actionable error messages:

- :mod:`mock_apis.shopfront`: OAuth2 client credentials with rotating refresh
  tokens, keyset cursor pagination.
- :mod:`mock_apis.ticketdesk`: API-key header, page-number pagination with
  ``Link`` / ``X-Total-Count`` headers.

Run them locally with ``uv run elt-mock <name>``.
"""
