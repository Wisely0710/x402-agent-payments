"""In-memory rate limiter helper for application-level request admission.

Replaces the vendored ``fast_x402.security.RateLimiter`` (which is being
removed from the migration path). The semantics are a **fixed window per key**:
the first request for a ``key`` anchors a window, that key is allowed up to
``max_requests`` hits while the window lasts, and its budget resets once
``window_seconds`` have elapsed since the anchor.

The provider itself does not rate-limit: this is a standalone helper the
application layer calls itself (e.g. before handling a request), not wiring
inside :class:`x402_v2.provider.X402V2Provider`.
"""

from __future__ import annotations

import math
import time


class RateLimiter:
    """Simple in-memory fixed-window-per-key rate limiter.

    Thread-safety mirrors the old provider usage: the limiter is only touched
    from the FastAPI event loop within a single process, so no locking is
    added here (matching the vendored behaviour).
    """

    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, dict] = {}

    def is_allowed(self, key: str) -> bool:
        """Return ``True`` when ``key`` is within its request budget."""
        now = time.time()

        # Drop entries whose window has fully elapsed.
        self.requests = {
            k: v for k, v in self.requests.items() if now - v["first_request"] < self.window_seconds
        }

        if key not in self.requests:
            self.requests[key] = {"count": 1, "first_request": now}
            return True

        entry = self.requests[key]
        if now - entry["first_request"] >= self.window_seconds:
            # Window elapsed: reset budget.
            entry["count"] = 1
            entry["first_request"] = now
            return True

        if entry["count"] >= self.max_requests:
            return False

        entry["count"] += 1
        return True

    def retry_after_seconds(self, key: str) -> int:
        """Seconds until ``key``'s current window elapses, or ``0`` when no
        active entry exists. Read-only: consumes no budget and never creates
        an entry. Returns ``ceil(first_request + window_seconds - now)``, so
        any active entry yields a positive integer (RFC 9110 ``Retry-After``
        delta-seconds)."""
        now = time.time()

        # Drop entries whose window has fully elapsed.
        self.requests = {
            k: v for k, v in self.requests.items() if now - v["first_request"] < self.window_seconds
        }

        entry = self.requests.get(key)
        if entry is None:
            return 0
        return max(1, math.ceil(entry["first_request"] + self.window_seconds - now))
