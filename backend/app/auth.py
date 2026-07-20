"""
Authentication helpers for VeryDisco.

Strategy:
  - Users authenticate with their Navidrome username + password.
  - We call Navidrome's /rest/getUser.view to validate and fetch isAdmin.
  - On success we issue a signed JWT stored in an httpOnly, Secure,
    SameSite=Strict cookie — it never touches JavaScript.
  - All protected routes use `get_current_user` as a FastAPI dependency.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import httpx
from fastapi import Cookie, HTTPException, Request, Response, status
from jose import JWTError, jwt

from backend.app.logger import get_logger

logger = get_logger()

ALGORITHM = "HS256"
COOKIE_NAME = "vd_session"

# ── Helpers ──────────────────────────────────────────────────────────────────

def generate_secret_key() -> str:
    """Generate a cryptographically secure random secret key."""
    return uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex chars


def create_access_token(
    user_id: str,
    username: str,
    is_admin: bool,
    secret_key: str,
    expires_delta: timedelta,
) -> str:
    """Sign a JWT containing user identity and admin flag."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "is_admin": is_admin,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, secret_key: str) -> Dict[str, Any]:
    """Decode and verify a JWT. Raises JWTError on failure."""
    return jwt.decode(token, secret_key, algorithms=[ALGORITHM])


def set_auth_cookie(
    response: Response,
    token: str,
    session_days: int,
    secure: bool = True,
) -> None:
    """
    Attach the session cookie to the response.

    httponly=True  → JS cannot read it (XSS protection)
    secure=True    → only sent over HTTPS (set secure=False for plain http homelab)
    samesite=lax   → compatible with reverse proxies and Cloudflare Access redirects
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=session_days * 86400,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Remove the session cookie (logout)."""
    response.delete_cookie(key=COOKIE_NAME, path="/", samesite="lax")


# ── Navidrome validation ──────────────────────────────────────────────────────

async def validate_navidrome_login(
    navidrome_url: str,
    username: str,
    password: str,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Validate credentials against Navidrome's Subsonic API.

    Calls /rest/getUser.view which returns the user's profile including isAdmin.
    Returns a dict with keys: id, username, name, isAdmin.
    Raises HTTPException 401 on bad credentials, 503 if Navidrome is unreachable.
    """
    if not navidrome_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Navidrome URL is not configured. Cannot authenticate.",
        )

    params = {
        "u": username,
        "p": password,
        "v": "1.16.1",
        "c": "VeryDisco",
        "f": "json",
        "username": username,  # getUser.view needs this
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            url = navidrome_url.rstrip("/") + "/rest/getUser.view"
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        subsonic = data.get("subsonic-response", {})
        if subsonic.get("status") != "ok":
            error = subsonic.get("error", {})
            logger.warning(
                f"Navidrome auth failed for '{username}': {error.get('message', 'unknown error')}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Navidrome username or password.",
            )

        user_data = subsonic.get("user", {})
        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )

        return {
            "id": str(user_data.get("username", username)),  # Use username as stable ID
            "username": user_data.get("username", username),
            "name": user_data.get("name") or user_data.get("username", username),
            "is_admin": bool(user_data.get("adminRole", False)),
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Navidrome username or password.",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Navidrome returned an error: {e.response.status_code}",
        )
    except Exception as e:
        logger.error(f"Failed to reach Navidrome for auth: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not reach Navidrome to verify credentials: {e}",
        )


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
) -> Dict[str, Any]:
    """
    FastAPI dependency that extracts and verifies the session cookie.

    Returns a dict: {id, username, is_admin}
    Raises 401 if the cookie is missing, invalid, or expired.
    """
    # Import here to avoid circular imports
    from backend.app.main import config_manager

    token: Optional[str] = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )

    cfg = config_manager.config
    if not cfg:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App not configured.",
        )

    secret_key = getattr(cfg, "auth", None)
    secret_key = cfg.auth.secret_key if secret_key else None
    if not secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth secret key not configured.",
        )

    try:
        payload = decode_access_token(token, secret_key)
        user_id: str = payload.get("sub", "")
        username: str = payload.get("username", "")
        is_admin: bool = payload.get("is_admin", False)
        if not user_id or not username:
            raise JWTError("Missing claims")
        return {"id": user_id, "username": username, "is_admin": is_admin}
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Session expired or invalid. Please log in again. ({e})",
        )


async def require_admin(
    current_user: Dict[str, Any],
) -> Dict[str, Any]:
    """
    FastAPI dependency that enforces admin access.
    Chain after get_current_user.
    """
    if not current_user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user
