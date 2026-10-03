from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_artwork_save_uses_detected_png_format_and_rejects_other_bytes(tmp_path, monkeypatch):
    from backend.app import main, auth

    album = tmp_path / "Album"
    album.mkdir()

    async def user(request):
        return {"id": "one", "is_admin": False}

    class Database:
        async def get_user_by_id(self, user_id):
            return None

        async def refresh_library_paths(self, user_id, paths):
            return None

    async def no_scan():
        return None

    payload = b"\x89PNG\r\n\x1a\n" + b"sample"

    class Response:
        headers = {"content-type": "image/png"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield payload

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args):
            return Response()

    monkeypatch.setattr(auth, "get_current_user", user)
    monkeypatch.setattr(main, "db", Database())
    monkeypatch.setattr(main.config_manager, "config", SimpleNamespace(paths=SimpleNamespace(music_dir=str(tmp_path))))
    monkeypatch.setattr(main, "trigger_navidrome_scan_debounced", no_scan)
    monkeypatch.setattr(main.httpx, "AsyncClient", Client)
    request = main.SaveArtRequest(folder_path=str(album), url="https://cdn-images.dzcdn.net/sample", embed=False)
    result = await main.save_art_endpoint(request, None)
    assert result["status"] == "success"
    assert (album / "cover.png").read_bytes() == payload
    assert (album / "folder.png").read_bytes() == payload
    assert not (album / "cover.jpg").exists()

    payload = b"not an image"
    with pytest.raises(HTTPException) as error:
        await main.save_art_endpoint(request, None)
    assert error.value.status_code == 400
