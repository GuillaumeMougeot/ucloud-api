"""Shared retry policy for transient gateway errors.

The SDU gateway intermittently returns 502/503/504 on control-plane calls —
this hits both the main request path (:mod:`client`) and token refresh
(:mod:`auth`), since refreshing is just another POST through the same gateway.
Both retry with the same backoff so a flaky gateway doesn't fail requests that
never reached the origin server.
"""

from __future__ import annotations

import time

#: Transient gateway/upstream errors worth retrying.
RETRY_STATUS = frozenset({502, 503, 504})
MAX_RETRIES = 5


def backoff_sleep(attempt: int) -> None:
    """Sleep the backoff for retry ``attempt`` (0-indexed): 0.5s, 1s, 2s, 4s, 4s, ..."""
    time.sleep(min(2**attempt, 8) * 0.5)
