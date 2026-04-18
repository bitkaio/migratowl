# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared httpx client pool — reuses TCP connections across changelog and registry calls."""

from __future__ import annotations

import asyncio
import logging

import httpx

from migratowl.config import get_settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class RetryTransport(httpx.AsyncBaseTransport):
    """Wraps an async transport with retry logic and exponential backoff."""

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        *,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._wrapped = wrapped
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: httpx.ConnectError | None = None
        resp: httpx.Response | None = None

        for attempt in range(self._max_retries + 1):
            last_exc = None
            try:
                resp = await self._wrapped.handle_async_request(request)
            except httpx.ConnectError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._compute_delay(attempt, None))
                    continue
                raise

            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                return resp

            if attempt < self._max_retries:
                await resp.aread()
                await asyncio.sleep(self._compute_delay(attempt, resp))
                continue

            return resp

        # Unreachable, but satisfies type checker.
        if last_exc is not None:
            raise last_exc
        assert resp is not None
        return resp

    def _compute_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return float(retry_after)  # noqa: TRY300
                except ValueError as exc:
                    logger.debug("Retry-After header is not a float: %s", exc)
        delay: float = self._backoff_base * (2**attempt)
        return delay

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def get_http_client() -> httpx.AsyncClient:
    """Return the shared httpx client, creating it lazily on first call."""
    global _client
    if _client is None:
        settings = get_settings()
        transport = RetryTransport(
            httpx.AsyncHTTPTransport(),
            max_retries=settings.http_retry_count,
            backoff_base=settings.http_retry_backoff_base,
        )
        _client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
            timeout=settings.http_timeout,
        )
    return _client


async def close_http_client() -> None:
    """Close the shared client and reset the singleton. Safe to call when no client exists."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
