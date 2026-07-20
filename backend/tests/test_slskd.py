import pytest
import respx
import httpx
from backend.app.clients.slskd import SlskdClient

@pytest.mark.asyncio
@respx.mock
async def test_search_candidates_filtering_and_sorting():
    client = SlskdClient(base_url="http://localhost:5030")
    
    # Mock create search
    respx.post("http://localhost:5030/api/v0/searches").respond(
        status_code=200, json={"id": "search-123"}
    )
    
    # Mock search status (complete)
    respx.get("http://localhost:5030/api/v0/searches/search-123").respond(
        status_code=200, json={"isComplete": True}
    )

    # Mock search responses (multiple peers & files)
    mock_responses = [
        {
            "username": "peer_slow",
            "hasFreeUploadSlot": True,
            "queueLength": 0,
            "speed": 50,
            "files": [
                {"filename": "Daft Punk - One More Time.mp3", "size": 10000000, "bitrate": 320}
            ]
        },
        {
            "username": "peer_fast_busy",
            "hasFreeUploadSlot": False,
            "queueLength": 5,
            "speed": 5000,
            "files": [
                {"filename": "Daft Punk - One More Time.mp3", "size": 10000000, "bitrate": 320}
            ]
        },
        {
            "username": "peer_low_bitrate",
            "hasFreeUploadSlot": True,
            "queueLength": 0,
            "speed": 2000,
            "files": [
                {"filename": "Daft Punk - One More Time.mp3", "size": 6000000, "bitrate": 192}
            ]
        },
        {
            "username": "peer_best",
            "hasFreeUploadSlot": True,
            "queueLength": 0,
            "speed": 2000,
            "files": [
                {"filename": "Daft Punk - One More Time.mp3", "size": 10000000, "bitrate": 320}
            ]
        }
    ]
    respx.get("http://localhost:5030/api/v0/searches/search-123/responses").respond(
        status_code=200, json=mock_responses
    )

    audio_quality = {"preset": "custom", "custom_profiles": [{"format": "mp3", "min_bitrate": 320}]}
    candidates, search_id = await client.search_candidates(
        artist="Daft Punk",
        title="One More Time",
        query="Daft Punk - One More Time",
        audio_quality=audio_quality
    )
    
    # Check that low bitrate (192) candidate is filtered out
    # Only peer_slow, peer_fast_busy, peer_best should remain (3 candidates)
    assert len(candidates) == 3
    
    # Check sorting order:
    # 1. peer_best (hasFreeUploadSlot=True, queue=0, bitrate=320, speed=2000)
    # 2. peer_slow (hasFreeUploadSlot=True, queue=0, bitrate=320, speed=50)
    # 3. peer_fast_busy (hasFreeUploadSlot=False, queue=5, bitrate=320, speed=5000)
    assert candidates[0]["username"] == "peer_best"
    assert candidates[1]["username"] == "peer_slow"
    assert candidates[2]["username"] == "peer_fast_busy"
