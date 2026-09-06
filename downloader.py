import os
import sys
import json
import asyncio
import subprocess
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

    movie_list = [m.strip() for m in RAW_MOVIE_QUERY.split(",") if m.strip()]
    print(f"Batch processing {len(movie_list)} movies: {movie_list}")

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    for current_movie in movie_list:
        print(f"\n==============================================")
        print(f"Now processing: '{current_movie}'")
        print(f"==============================================")
        
        # 1. FORCE HINDI: Append "Hindi" to every single search query
        query_sent = f"{current_movie} Hindi"
        query_words = current_movie.lower().split()
        msg = None
        
        for target_bot in BOT_USERNAMES:
            print(f"\n--- Trying bot: {target_bot} with query '{query_sent}' ---")
            await client.send_message(target_bot, query_sent)
            
            clicked_messages = set()
            
            # We allow up to 40 checks to give menus time to load
            for _ in range(40):
                await asyncio.sleep(2)
                history = await client.get_messages(target_bot, limit=10)
                
                for m in history:
                    if m.out:
                        continue 
                    
                    if m.video or m.document:
                        msg = m
                        break
                    
                    # 2. DYNAMIC BUTTON SCORING (Handles ANY bot menu)
                    if m.buttons and m.id not in clicked_messages:
                        best_btn = None
                        best_score = -1
                        
                        for row in m.buttons:
                            for btn in row:
                                if not btn.text: continue
                                t = btn.text.lower()
                                score = 0
                                
                                # Highly prioritize Hindi options
                                if "hindi" in t or "dual" in t: score += 10
                                # Prioritize exact movie name matches (for search result menus)
                                if all(w in t for w in query_words): score += 8
                                # Prioritize TV-safe MP4 formats
                                if "mp4" in t or "h.264" in t: score += 5
                                # General resolutions
                                if "720" in t or "480" in t or "1080" in t: score += 3
                                
                                if score > best_score:
                                    best_score = score
                                    best_btn = btn
                        
                        if best_btn and best_score > 0:
                            print(f"--> Smart clicking button: '{best_btn.text}' (Score: {best_score})")
                            await best_btn.click()
                            clicked_messages.add(m.id)
                        elif m.buttons:
                            # Fallback if no keywords match
                            first_btn = m.buttons[0][0]
                            print(f"--> No keywords matched. Clicking first button: '{first_btn.text}'")
                            await first_btn.click()
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

        raw_file_name = getattr(msg.file, 'name', None) or f"{current_movie.replace(' ', '_')}.mp4"
        print(f"\nDownloading {raw_file_name} on GitHub runner...")
        download_path = await msg.download_media(file=raw_file_name)
        
        # --- 3. HIGH-SPEED TV-SAFE CONVERSION ---
        base_name, _ = os.path.splitext(raw_file_name)
        safe_file_name = base_name + ".mp4"
        safe_download_path = "converted_" + safe_file_name

        print(f"Converting file using HIGH-SPEED FFmpeg Profile...")
        # -preset ultrafast and -threads 0 speed up the process massively
        ffmpeg_cmd = [
            "ffmpeg", "-i", download_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-threads", "0",
            "-c:a", "aac", "-b:a", "128k",
            safe_download_path
        ]
        
        process = subprocess.run(ffmpeg_cmd)
        
        if process.returncode == 0:
            print("Conversion successful!")
            if os.path.exists(download_path):
                os.remove(download_path)
            final_path = safe_download_path
            final_name = safe_file_name
        else:
            print("Conversion warning: FFmpeg failed, uploading original file...")
            final_path = download_path
            final_name = raw_file_name

        print(f"Uploading {final_name} to Google Drive...")
        service = get_drive_service()
        folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
        file_metadata = {'name': final_name, 'parents': [folder_id]}
        media = MediaFileUpload(final_path, resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        
        if os.path.exists(final_path):
            os.remove(final_path)
        print(f"Upload complete for '{current_movie}'!")

    print("\nBatch complete! Syncing final library state...")
    sync_movies_json()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(download_worker())
