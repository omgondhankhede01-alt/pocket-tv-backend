import os
import sys
import json
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
SESSION_STRING = os.environ.get("TG_SESSION", "")
BOT_USERNAMES = ["@iPapkornA2bot", "@iPopcornMBot", "@kevinhartrobot"]
RAW_MOVIE_QUERY = os.environ.get("MOVIE_NAME", "")

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
    if not SESSION_STRING or not RAW_MOVIE_QUERY:
        print("Missing TG_SESSION or MOVIE_NAME environment variable!")
        sys.exit(1)

    # Split the comma-separated string into a batch list
    movie_list = [m.strip() for m in RAW_MOVIE_QUERY.split(",") if m.strip()]
    print(f"Batch processing {len(movie_list)} movies: {movie_list}")

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    # Loop through the list of movies
    for current_movie in movie_list:
        print(f"\n==============================================")
        print(f"Now processing: '{current_movie}'")
        print(f"==============================================")
        
        query_words = current_movie.lower().split()
        msg = None
        
        for target_bot in BOT_USERNAMES:
            print(f"\n--- Trying bot: {target_bot} for '{current_movie}' ---")
            await client.send_message(target_bot, current_movie)
            
            clicked_messages = set()
            
            for _ in range(30):
                await asyncio.sleep(2)
                history = await client.get_messages(target_bot, limit=10)
                
                for m in history:
                    if m.out:
                        continue 
                    
                    if m.video or m.document:
                        msg = m
                        break
                    
                    if m.buttons and m.id not in clicked_messages:
                        print("Menu detected. Reading buttons...")
                        button_clicked = False
                        
                        for row in m.buttons:
                            for button in row:
                                if not button.text: continue
                                btn_text = button.text.lower()
                                
                                if any(q in btn_text for q in ["1080", "720", "480", "2160", "mkv", "mp4", "hevc"]):
                                    print(f"--> Found quality option: '{button.text}'. Clicking it!")
                                    await button.click()
                                    button_clicked = True
                                    break
                                
                                if all(word in btn_text for word in query_words):
                                    print(f"--> Found matching movie: '{button.text}'. Clicking it!")
                                    await button.click()
                                    button_clicked = True
                                    break
                            
                            if button_clicked:
                                break
                        
                        if not button_clicked and m.buttons:
                            first_btn = m.buttons[0][0].text
                            print(f"--> No exact match found. Falling back to first option: '{first_btn}'")
                            await m.click(0)
                            
                        clicked_messages.add(m.id)
                
                if msg:
                    break
            
            if msg:
                print(f"Video acquired from {target_bot}!")
                break
            else:
                print(f"Failed to get video from {target_bot}. Moving to next bot...")

        if not msg:
            print(f"\nAll bots failed to return a video file for '{current_movie}'. Skipping...")
            continue 

        # Download
        file_name = getattr(msg.file, 'name', None) or f"{current_movie.replace(' ', '_')}.mp4"
        print(f"\nDownloading {file_name} on GitHub runner...")
        download_path = await msg.download_media(file=file_name)
        
        # Upload
        print(f"Uploading {file_name} to Google Drive...")
        service = get_drive_service()
        folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaFileUpload(download_path, resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        
        if os.path.exists(download_path):
            os.remove(download_path)
        print(f"Upload complete for '{current_movie}'!")

    # Sync JSON exactly once at the end of the entire batch
    print("\nBatch complete! Syncing final library state...")
    sync_movies_json()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(download_worker())
