"""Check the built image, without contacting any configured external service."""
import json
import os
from urllib.error import HTTPError
from urllib.request import urlopen


def main():
    assert os.geteuid() != 0, "The application must run without root privileges"
    with urlopen("http://127.0.0.1:8080/healthz", timeout=10) as response:
        assert json.load(response)["status"] == "healthy"
    with urlopen("http://127.0.0.1:8080/api/status", timeout=10) as response:
        assert json.load(response)["is_configured"] is False
    with urlopen("http://127.0.0.1:8080/", timeout=10) as response:
        assert response.status == 200 and b"<html" in response.read().lower()
    try:
        urlopen("http://127.0.0.1:8080/api/config", timeout=10)
    except HTTPError as error:
        assert error.code == 401, f"Expected authentication failure, received {error.code}"
    else:
        raise AssertionError("Configuration is accessible without authentication")
    print("Production image: non-root startup, SQLite, frontend, and authentication OK")


if __name__ == "__main__":
    main()
