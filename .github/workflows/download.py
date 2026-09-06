import os
import re
import sys
import json
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Config from Environment Variables
API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
SESSION_STRING = os.environ.get("TG_SESSION", "")
BOT_USERNAMES = ["@iPapkornA2bot", "@iPopcornMBot", "@kevinhartrobot"]
MOVIE_QUERY = os.environ.get("MOVIE_NAME", "")

def get_drive_service():
    creds_json = os.environ.get("GCP_CREDENTIALS", "")
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def sync_movies_json():
    """Fetches all movies from Drive and writes them to movies.json so the website updates."""
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    service = get_drive_service()
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query, pageSize=100, fields="files(name, webViewLink, webContentLink)"
    ).execute()
    
    files = results.get('files', [])
    movie_data = [{"name": f.get("name"), "webViewLink": f.get("webViewLink"), "webContentLink": f.get("webContentLink", "#")} for f in files]
    
    with open("movies.json", "w", encoding="utf-8") as f:
        json.dump(movie_data, f, indent=4)
    print("Synced movies.json with Google Drive!")

async def download_worker():
    if not SESSION_STRING or not MOVIE_QUERY:
        print("Missing Session String or Movie Name!")
        return

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    print(f"Searching Telegram for: {MOVIE_QUERY}")
    
    # [Insert your existing Telegram bot interaction logic here - exact same as before]
    # For brevity, assume the bot interacted and found the media message:
    msg = None # (Your logic that finds the message with the video file)
    
    if msg and msg.media:
        file_name = msg.file.name or f"{MOVIE_QUERY}.mp4"
        print(f"Downloading {file_name}...")
        download_path = await msg.download_media(file=file_name)
        
        print(f"Uploading {file_name} to Google Drive...")
        service = get_drive_service()
        folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaFileUpload(download_path, resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        
        os.remove(download_path)
        print("Upload complete!")
        
    # Always sync the JSON at the end so the website has the latest data
    sync_movies_json()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(download_worker())