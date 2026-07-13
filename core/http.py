"""Shared HTTP session with connection pooling and retry/backoff.

All source modules use this one session so we get keep-alive connection reuse
(faster) and automatic retries on transient server errors. We deliberately do
NOT retry 429 (rate limit) or 4xx — those are surfaced to the caller so the UI
can show a clear message rather than silently hammering an API.
"""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 v1/v2 keep Retry in the same place, but guard just in case
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover
    Retry = None  # type: ignore

import config


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    if Retry is not None:
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,  # 0s, 0.5s, 1s between attempts
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    return session


# One process-wide session, imported by the source modules.
SESSION = _build_session()
