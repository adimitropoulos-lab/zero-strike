from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


_TRANSIENT = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


def _retry():
    return retry(
        retry=retry_if_exception_type(_TRANSIENT),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )


class HttpClient:
    """Thin httpx wrapper with retries and JSON helpers. Sync — these endpoints are fast enough."""

    def __init__(self, base_url: str, timeout: float = 20.0, headers: dict | None = None):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers or {},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @_retry()
    def get_json(self, path: str, **params):
        r = self._client.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    @_retry()
    def post_json(self, path: str, json: dict):
        r = self._client.post(path, json=json)
        r.raise_for_status()
        return r.json()
