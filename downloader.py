import os
import sys
import json
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

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
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    service = get_drive_service()
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query, pageSize=100, fields="files(name, webViewLink, webContentLink)"
    ).execute()
    
    files = results.get('files', [])
    movie_data = [
        {
            "name": f.get("name"),
            "webViewLink": f.get("webViewLink"),
            "webContentLink": f.get("webContentLink", "#")
        } for f in files
    ]
    
    with open("movies.json", "w", encoding="utf-8") as f:
        json.dump(movie_data, f, indent=4)
    print("Successfully synced movies.json with Google Drive!")

async def download_worker():
    if not SESSION_STRING or not MOVIE_QUERY:
        print("Missing TG_SESSION or MOVIE_NAME environment variable!")
        sys.exit(1)

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    print(f"Connected to Telegram. Searching for: '{MOVIE_QUERY}'")
    
    msg = None
    
    # Loop through the bots one by one
    for target_bot in BOT_USERNAMES:
        print(f"\n--- Trying bot: {target_bot} ---")
        await client.send_message(target_bot, MOVIE_QUERY)
        
        clicked_messages = set() # Keep track of buttons we've already clicked
        
        # Wait up to 40 seconds per bot for a response
        for _ in range(20):
            await asyncio.sleep(2)
            history = await client.get_messages(target_bot, limit=5)
            
            for m in history:
                if m.out:
                    continue # Skip our own messages
                
                # Success! The bot sent the actual video file
                if m.video or m.document:
                    msg = m
                    break
                
                # The bot sent a menu with buttons!
                if m.buttons and m.id not in clicked_messages:
                    print("Bot sent a menu with buttons. Clicking the first option...")
                    try:
                        # click(0) presses the very first button in the menu
                        await m.click(0)
                        clicked_messages.add(m.id)
                    except Exception as e:
                        print(f"Could not click button: {e}")
            
            # If we found the video file, break out of the waiting loop
            if msg:
                break
        
        # If we found the video, break out of the bot loop
        if msg:
            print(f"Video acquired from {target_bot}!")
            break
        else:
            print(f"Failed to get video from {target_bot}. Moving to next bot...")

    # If ALL bots failed
    if not msg:
        print("\nAll bots failed to return a video file.")
        sync_movies_json()
        return

    # Download from Telegram to runner disk
    file_name = getattr(msg.file, 'name', None) or f"{MOVIE_QUERY}.mp4"
    print(f"\nDownloading {file_name} on GitHub runner...")
    download_path = await msg.download_media(file=file_name)
    
    # Upload straight to Google Drive
    print(f"Uploading {file_name} to Google Drive...")
    service = get_drive_service()
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    file_metadata = {'name': file_name, 'parents': [folder_id]}
    media = MediaFileUpload(download_path, resumable=True)
    service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    
    # Clean up runner disk
    if os.path.exists(download_path):
        os.remove(download_path)
    print("Upload complete!")

    # Update movies.json
    sync_movies_json()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(download_worker())
