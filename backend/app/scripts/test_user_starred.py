import asyncio
import logging
import sys
import os

# Import verydisco application modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from backend.app.main import config_manager, db
from backend.app.scheduler import SchedulerManager
from backend.app.clients.navidrome import NavidromeClient

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("verydisco-user-test")

async def run_user_test():
    await db.initialize()
    config_manager.load()
    config = config_manager.config
    
    if not config:
        logger.error("Configuration not loaded. Please make sure VeryDisco is configured.")
        return

    # List all users in DB
    async with db.get_db() as conn:
        async with conn.execute("SELECT id, username, subsonic_token, subsonic_salt, music_dir FROM users") as cursor:
            users = [dict(r) for r in cursor.fetchall()]

    if not users:
        logger.error("No users found in database.")
        return

    print("\nAvailable Users in Database:")
    for idx, u in enumerate(users):
        has_creds = "Yes" if (u.get("subsonic_token") and u.get("subsonic_salt")) else "No"
        print(f"[{idx}] ID: {u['id']}, Username: {u['username']}, Subsonic Creds: {has_creds}, MusicDir: {u['music_dir']}")

    if len(sys.argv) < 2:
        print("\nUsage: python backend/app/scripts/test_user_starred.py <username_or_index>")
        return

    target = sys.argv[1]
    selected_user = None

    # Try matching by index
    if target.isdigit():
        idx = int(target)
        if 0 <= idx < len(users):
            selected_user = users[idx]

    # Try matching by username
    if not selected_user:
        for u in users:
            if u["username"].lower() == target.lower():
                selected_user = u
                break

    if not selected_user:
        logger.error(f"User '{target}' not found.")
        return

    print(f"\n--- Testing User: {selected_user['username']} ---")
    uid = selected_user["id"]
    sub_token = selected_user.get("subsonic_token")
    sub_salt = selected_user.get("subsonic_salt")

    nd_username = selected_user["username"]
    nd_password = None
    if not sub_token or not sub_salt:
        if selected_user["username"] == config.navidrome.username:
            print("No token/salt found. Falling back to admin config.yml credentials.")
            nd_username = config.navidrome.username
            nd_password = config.navidrome.password
        else:
            logger.error("No Subsonic credentials found for this user. The user must log in through the WebUI first.")
            return

    client = NavidromeClient(
        url=config.navidrome.url,
        username=nd_username,
        password=nd_password,
        token=sub_token,
        salt=sub_salt
    )

    print("Testing connection to Navidrome...")
    try:
        status_msg = await client.test_connection()
        print(f"Success: {status_msg}")
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        return

    print("Fetching starred tracks from Navidrome...")
    try:
        tracks = await client.get_starred_tracks()
        print(f"Found {len(tracks)} starred tracks:")
        for t in tracks:
            print(f"  - {t['artist']} - {t['title']} (Album: {t['album']}, MBID: {t['mbid']})")
    except Exception as e:
        logger.error(f"Failed to fetch starred tracks: {e}")
        return

    print("\nTriggering starred sync for user...")
    manager = SchedulerManager(db)
    await manager.sync_navidrome_starred(config, user_id=uid)
    print("Sync complete.")

if __name__ == "__main__":
    asyncio.run(run_user_test())
