import os
import tempfile
import asyncio
import pytest
import pytest_asyncio
from backend.app.database import Database

@pytest.fixture(scope="session")
def event_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

@pytest_asyncio.fixture
async def test_db():
    """Provides a fresh, isolated temporary SQLite database for testing."""
    fd, db_path = tempfile.mkstemp(suffix="_test.db")
    os.close(fd)
    
    db = Database(db_path)
    await db.initialize()
    
    yield db
    
    # Cleanup database file
    try:
        os.unlink(db_path)
    except OSError:
        pass
