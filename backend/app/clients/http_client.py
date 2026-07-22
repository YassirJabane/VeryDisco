import httpx
from typing import Optional
from backend.app.logger import get_logger

logger = get_logger()

_global_http_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    """
    Returns a shared, persistent httpx.AsyncClient instance with connection pooling and keep-alive.
    Prevents TCP/TLS connection churn and socket exhaustion across backend API clients.
    """
    global _global_http_client
    if _global_http_client is None or _global_http_client.is_closed:
        limits = httpx.Limits(max_keepalive_connections=30, max_connections=100, keepalive_expiry=60.0)
        timeout = httpx.Timeout(20.0, connect=10.0)
        headers = {"User-Agent": "VeryDisco/1.0.0"}
        _global_http_client = httpx.AsyncClient(limits=limits, timeout=timeout, headers=headers, follow_redirects=True)
        logger.info("Initialized shared HTTP connection pool (httpx.AsyncClient).")
    return _global_http_client

async def close_http_client():
    """Closes the global shared HTTP client session on app shutdown."""
    global _global_http_client
    if _global_http_client is not None and not _global_http_client.is_closed:
        await _global_http_client.aclose()
        _global_http_client = None
        logger.info("Closed shared HTTP connection pool.")
