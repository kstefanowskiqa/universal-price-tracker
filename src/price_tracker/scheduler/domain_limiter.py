from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class DomainRateLimiter:
    """Kontroluje globalną równoległość oraz limit max 1 zapytania naraz + odstęp per domena."""

    def __init__(
        self,
        *,
        max_global_concurrent: int = 5,
        default_domain_delay_seconds: float = 5.0,
        jitter_ratio: float = 0.25,
    ) -> None:
        self._global_sem = asyncio.Semaphore(max_global_concurrent)
        self._default_delay = default_domain_delay_seconds
        self._jitter_ratio = jitter_ratio
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._last_request_at: dict[str, float] = {}

    def _get_lock(self, domain: str) -> asyncio.Lock:
        key = domain.lower().strip()
        if key not in self._domain_locks:
            self._domain_locks[key] = asyncio.Lock()
        return self._domain_locks[key]

    @asynccontextmanager
    async def acquire(
        self,
        domain: str,
        *,
        min_delay_seconds: float | None = None,
    ) -> AsyncIterator[None]:
        key = domain.lower().strip()
        delay = self._default_delay if min_delay_seconds is None else float(min_delay_seconds)
        domain_lock = self._get_lock(key)

        async with domain_lock:
            now = time.monotonic()
            last = self._last_request_at.get(key)
            if last is not None and delay > 0:
                jitter = (
                    random.uniform(0.0, delay * self._jitter_ratio)
                    if self._jitter_ratio > 0
                    else 0.0
                )
                wait_for = (last + delay + jitter) - now
                if wait_for > 0:
                    await asyncio.sleep(wait_for)

            async with self._global_sem:
                try:
                    yield
                finally:
                    self._last_request_at[key] = time.monotonic()
