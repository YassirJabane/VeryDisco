import os
import yaml
import tempfile
import pytest
from pydantic import ValidationError
from backend.app.config import ConfigManager, AppConfig

def test_generate_default_config():
    fd, path = tempfile.mkstemp(suffix=".yml")
    os.close(fd)
    os.unlink(path) # delete empty file to trigger generation logic
    try:
        manager = ConfigManager(path)
        # Should generate default on init since path doesn't exist
        assert os.path.exists(path)
        assert manager.is_configured is False
        assert "ListenBrainz username" in manager.validation_errors or "Placeholder" in manager.validation_errors
        
        # Verify default contents
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            assert data["listenbrainz"]["username"] == "your-username"
            assert data["slskd"]["base_url"] == "http://slskd:5030"
    finally:
        os.unlink(path)

def test_validation_fails_on_missing_required():
    fd, path = tempfile.mkstemp(suffix=".yml")
    os.close(fd)
    try:
        with open(path, "w") as f:
            # write incomplete config (missing base_url)
            f.write("""
listenbrainz:
  username: "myuser"
slskd:
  api_key: "abc"
""")
        manager = ConfigManager(path)
        assert manager.is_configured is False
        assert "base_url" in manager.validation_errors.lower()
    finally:
        os.unlink(path)

def test_successful_validation():
    fd, path = tempfile.mkstemp(suffix=".yml")
    os.close(fd)
    try:
        with open(path, "w") as f:
            f.write("""
listenbrainz:
  username: "actual-user"
  playlist_source: "weekly-exploration"
slskd:
  base_url: "http://slskd-service:5030"
  downloads_dir: "/downloads"
navidrome:
  url: "http://navidrome:4533"
""")
        manager = ConfigManager(path)
        assert manager.is_configured is True
        assert manager.validation_errors is None
        assert manager.config.listenbrainz.username == "actual-user"
        assert manager.config.slskd.base_url == "http://slskd-service:5030"
        assert manager.config.schedule.cron == "0 3 * * 1" # default value
    finally:
        os.unlink(path)
