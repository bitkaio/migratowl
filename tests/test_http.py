"""Tests for shared HTTP client with retry transport."""

from unittest.mock import AsyncMock, patch

import httpx

from migratowl.http import RetryTransport, close_http_client, get_http_client


class TestRetryTransport:
    async def test_returns_response_on_200(self) -> None:
        mock_transport = AsyncMock(spec=httpx.AsyncBaseTransport)
        mock_transport.handle_async_request.return_value = httpx.Response(200)

        transport = RetryTransport(mock_transport, max_retries=3)
        request = httpx.Request("GET", "https://example.com")
        response = await transport.handle_async_request(request)

        assert response.status_code == 200
        assert mock_transport.handle_async_request.call_count == 1

    async def test_returns_response_on_404(self) -> None:
        mock_transport = AsyncMock(spec=httpx.AsyncBaseTransport)
        mock_transport.handle_async_request.return_value = httpx.Response(404)

        transport = RetryTransport(mock_transport, max_retries=3)
        request = httpx.Request("GET", "https://example.com")
        response = await transport.handle_async_request(request)

        assert response.status_code == 404
        assert mock_transport.handle_async_request.call_count == 1

    async def test_retries_on_429(self) -> None:
        mock_transport = AsyncMock(spec=httpx.AsyncBaseTransport)
        retry_resp = httpx.Response(429)
        ok_resp = httpx.Response(200)
        mock_transport.handle_async_request.side_effect = [retry_resp, ok_resp]

        transport = RetryTransport(mock_transport, max_retries=3, backoff_base=0.0)
        request = httpx.Request("GET", "https://example.com")
        response = await transport.handle_async_request(request)

        assert response.status_code == 200
        assert mock_transport.handle_async_request.call_count == 2

    async def test_retries_on_503(self) -> None:
        mock_transport = AsyncMock(spec=httpx.AsyncBaseTransport)
        retry_resp = httpx.Response(503)
        ok_resp = httpx.Response(200)
        mock_transport.handle_async_request.side_effect = [retry_resp, ok_resp]

        transport = RetryTransport(mock_transport, max_retries=3, backoff_base=0.0)
        request = httpx.Request("GET", "https://example.com")
        response = await transport.handle_async_request(request)

        assert response.status_code == 200
        assert mock_transport.handle_async_request.call_count == 2

    async def test_returns_last_retry_response_on_exhaustion(self) -> None:
        mock_transport = AsyncMock(spec=httpx.AsyncBaseTransport)
        mock_transport.handle_async_request.return_value = httpx.Response(429)

        transport = RetryTransport(mock_transport, max_retries=2, backoff_base=0.0)
        request = httpx.Request("GET", "https://example.com")
        response = await transport.handle_async_request(request)

        assert response.status_code == 429
        assert mock_transport.handle_async_request.call_count == 3  # initial + 2 retries

    async def test_respects_retry_after_header(self) -> None:
        mock_transport = AsyncMock(spec=httpx.AsyncBaseTransport)
        retry_resp = httpx.Response(429, headers={"Retry-After": "0.01"})
        ok_resp = httpx.Response(200)
        mock_transport.handle_async_request.side_effect = [retry_resp, ok_resp]

        transport = RetryTransport(mock_transport, max_retries=3, backoff_base=100.0)
        request = httpx.Request("GET", "https://example.com")

        with patch("migratowl.http.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await transport.handle_async_request(request)

        assert response.status_code == 200
        mock_sleep.assert_called_once_with(0.01)


class TestGetHttpClient:
    async def test_returns_async_client(self) -> None:
        with patch("migratowl.http.get_settings") as mock_settings:
            mock_settings.return_value.http_timeout = 30.0
            mock_settings.return_value.http_retry_count = 3
            mock_settings.return_value.http_retry_backoff_base = 0.5
            try:
                client = get_http_client()
                assert isinstance(client, httpx.AsyncClient)
            finally:
                await close_http_client()

    async def test_singleton_returns_same_instance(self) -> None:
        with patch("migratowl.http.get_settings") as mock_settings:
            mock_settings.return_value.http_timeout = 30.0
            mock_settings.return_value.http_retry_count = 3
            mock_settings.return_value.http_retry_backoff_base = 0.5
            try:
                client1 = get_http_client()
                client2 = get_http_client()
                assert client1 is client2
            finally:
                await close_http_client()

    async def test_close_resets_singleton(self) -> None:
        with patch("migratowl.http.get_settings") as mock_settings:
            mock_settings.return_value.http_timeout = 30.0
            mock_settings.return_value.http_retry_count = 3
            mock_settings.return_value.http_retry_backoff_base = 0.5
            client1 = get_http_client()
            await close_http_client()
            client2 = get_http_client()
            assert client1 is not client2
            await close_http_client()
