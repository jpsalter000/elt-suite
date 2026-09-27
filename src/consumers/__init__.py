"""Source-system client packages.

Each sub-package (``consumers.<source_system>``) exposes a ``client`` module whose
``fetch_<job_name>(ctx)`` functions satisfy :data:`elt_suite.contract.FetchFn`.
"""
