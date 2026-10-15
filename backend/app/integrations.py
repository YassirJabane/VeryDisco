from typing import Any


def listenbrainz_credentials(config: Any, user: dict, user_config: dict | None) -> tuple[str, str]:
    """Keep personal accounts isolated in both HTTP and background operations."""
    personal = user_config or {}
    username = personal.get("lb_username") or ""
    token = personal.get("lb_token") or ""
    if user.get("is_admin"):
        global_username = config.listenbrainz.username or ""
        username = username or global_username
        # Never combine a different personal username with the global token.
        if not token and username.casefold() == global_username.casefold():
            token = config.listenbrainz.token or ""
    return username, token


def navidrome_client_for_user(config: Any, user: dict):
    from backend.app.clients.navidrome import NavidromeClient
    nd = config.navidrome
    if not nd.url:
        return None
    token, salt = user.get("subsonic_token"), user.get("subsonic_salt")
    password = None
    if not (token and salt):
        if not user.get("is_admin") or user.get("username") != nd.username:
            return None
        token, salt, password = None, None, nd.password
    return NavidromeClient(url=nd.url, username=user["username"], password=password, token=token, salt=salt)
