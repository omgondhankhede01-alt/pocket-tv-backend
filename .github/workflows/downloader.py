import os
import sys
import json
import asyncio
import time
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
SESSION_STRING = os.environ.get("TG_SESSION", "")
MOVIE_NAME = os.environ.get("MOVIE_NAME", "").strip()

# Keep ONLY the 2 working bots here
ACTIVE_BOTS = [
    "@kevinhartrobot",     # Replace with your first verified bot
    "@iPapkornA2bot"      # Replace with your second verified bot
]

BOT_RESPONSE_TIMEOUT = 45  # Seconds to wait for a bot reply before skipping

def get_drive_service():
    client_id = os.environ.get("GCP_CLIENT_ID", "")
    client_secret = os.environ.get("GCP_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GCP_REFRESH_TOKEN", "")
    
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    return build('drive', 'v3', credentials=creds)

def sync_movies_json():
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    service = get_drive_service()
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query, pageSize=200, fields="files(id, name, webViewLink, webContentLink)"
    ).execute()
    
    files = results.get('files', [])
    movie_data = [
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "webViewLink": f.get("webViewLink"),
            "webContentLink": f.get("webContentLink", "#")
        } for f in files
    ]
    with open("movies.json", "w", encoding="utf-8") as f:
        json.dump(movie_data, f, indent=4)
    print("[*] movies.json synced.")

async def search_and_download_from_bot(client, bot_username, query):
    print(f"[*] Querying bot: {bot_username} for '{query}'...")
    try:
        # Send query and record message ID
        sent_msg = await client.send_message(bot_username, query)
        out_id = sent_msg.id
    except Exception as e:
        print(f"[-] Could not send message to {bot_username}: {e}")
        return None

    start_time = time.time()
    target_media_msg = None

    # Poll strictly for NEW messages with id > sent_msg.id
    while time.time() - start_time < BOT_RESPONSE_TIMEOUT:
        async for msg in client.iter_messages(bot_username, min_id=out_id, limit=5):
            if msg.media or msg.document or msg.video:
                target_media_msg = msg
                break
        
        if target_media_msg:
            break
        await asyncio.sleep(3)

    if not target_media_msg:
        print(f"[-] {bot_username} returned no valid media file within timeout.")
        return None

    file_name = getattr(target_media_msg.file, 'name', '') or f"{query}.mp4"
    print(f"[+] Direct file received: {file_name}")
    local_path = await target_media_msg.download_media(file=f"tmp_{file_name}")
    return local_path, file_name

def upload_to_drive(local_path, file_name):
    print(f"[*] Uploading {file_name} to Google Drive...")
    service = get_drive_service()
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    metadata = {'name': file_name, 'parents': [folder_id]}
    media = MediaFileUpload(local_path, resumable=True)
    service.files().create(body=metadata, media_body=media, fields='id').execute()
    
    if os.path.exists(local_path):
        os.remove(local_path)
    print(f"[✓] Upload completed: {file_name}")

async def main():
    if not MOVIE_NAME:
        print("[-] Error: MOVIE_NAME input is empty.")
        sys.exit(1)

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    downloaded_file = None
    for bot in ACTIVE_BOTS:
        result = await search_and_download_from_bot(client, bot, MOVIE_NAME)
        if result:
            downloaded_file, file_name = result
            upload_to_drive(downloaded_file, file_name)
            break

    await client.disconnect()

    if downloaded_file:
        sync_movies_json()
    else:
        print(f"[-] All bots failed to find '{MOVIE_NAME}'. Zero files downloaded.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
