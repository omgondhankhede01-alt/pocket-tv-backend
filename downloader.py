import os
import sys
import re
import json
import time
import asyncio
import subprocess
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
SESSION_STRING = os.environ.get("TG_SESSION", "")
MOVIE_NAME = os.environ.get("MOVIE_NAME", "").strip()

# Your 2 verified bots from your workflow
ACTIVE_BOTS = [
    "@kevinhartrobot",
    "@iPapkornA2bot"
]

BASE_DOMAIN = "https://www.filmyzilla65.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def clean_text(text):
    return re.sub(r'[^a-zA-Z0-9\s]', '', text).lower().strip()

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
    print("[*] movies.json synced successfully.")

# --- SOURCE 1: FILMYZILLA SCRAPER ---
def try_filmyzilla_scrape(query):
    print(f"\n[Source 1] Searching Filmyzilla for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    season_match = re.search(r'\bS(\d{1,2})\b', query, re.IGNORECASE)
    episode_match = re.search(r'\bE(\d{1,2})\b', query, re.IGNORECASE)
    base_title = re.sub(r'\bS\d{1,2}E\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    
    search_url = f"{BASE_DOMAIN}/search/{quote(base_title)}.html"
    try:
        r = session.get(search_url, timeout=12)
        if r.status_code != 200:
            return None
    except Exception as e:
        print(f"[-] Filmyzilla search error: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    clean_base = clean_text(base_title)
    candidates = []
    
    for a in soup.find_all("a", href=True):
        href = a['href']
        title = a.get_text(strip=True)
        if any(seg in href for seg in ["/movie/", "/series/"]):
            c_title = clean_text(title)
            score = 0
            if clean_base in c_title:
                score += 10
            if season_match:
                s_token = f"s{int(season_match.group(1)):02d}"
                if s_token in c_title:
                    score += 20
            if score > 0:
                candidates.append((score, title, urljoin(BASE_DOMAIN, href)))

    if not candidates:
        print("[-] No matching titles found on Filmyzilla.")
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    target_page = candidates[0][2]
    
    try:
        r2 = session.get(target_page, timeout=12)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        tier_candidates = []
        for a in soup2.find_all("a", href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if "/server/" in href:
                tier_candidates.append((text, urljoin(BASE_DOMAIN, href)))

        if not tier_candidates:
            return None

        selected_tier = tier_candidates[0]
        if episode_match:
            ep_num = int(episode_match.group(1))
            ep_pattern = re.compile(rf'\b(ep|episode|e)[-_\s]*0?{ep_num}\b', re.IGNORECASE)
            for text, link in tier_candidates:
                if ep_pattern.search(text) or f"ep {ep_num}" in text.lower():
                    selected_tier = (text, link)
                    break
        else:
            for text, link in tier_candidates:
                if "720p" in text.lower():
                    selected_tier = (text, link)
                    break

        session.headers.update({"Referer": target_page})
        r3 = session.get(selected_tier[1], timeout=12)
        soup3 = BeautifulSoup(r3.text, "html.parser")
        server_links = [urljoin(BASE_DOMAIN, a['href']) for a in soup3.find_all("a", href=True) if "/verified/" in a['href']]
        
        if not server_links:
            return None

        session.headers.update({"Referer": selected_tier[1]})
        res = session.get(server_links[0], stream=True, allow_redirects=True, timeout=15)
        if res.status_code == 200:
            print(f"[+] Direct media stream resolved: {res.url}")
            local_dl = "temp_raw_stream.mkv"
            with open(local_dl, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
            return local_dl
    except Exception as e:
        print(f"[-] Error downloading from Filmyzilla stream: {e}")
        return None

    return None

# --- SOURCE 2: TELEGRAM BOTS FALLBACK ---
async def try_telegram_bots(query):
    print(f"\n[Source 2] Falling back to Telegram bots for: '{query}'...")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    for bot in ACTIVE_BOTS:
        print(f"[*] Querying bot: {bot}...")
        try:
            sent_msg = await client.send_message(bot, query)
            out_id = sent_msg.id
        except Exception as e:
            print(f"[-] Could not message {bot}: {e}")
            continue

        start_time = time.time()
        target_media_msg = None

        while time.time() - start_time < 45:
            async for msg in client.iter_messages(bot, min_id=out_id, limit=5):
                if msg.media or msg.document or msg.video:
                    target_media_msg = msg
                    break
            if target_media_msg:
                break
            await asyncio.sleep(3)

        if target_media_msg:
            f_name = getattr(target_media_msg.file, 'name', '') or "video.mp4"
            print(f"[+] Direct file received from {bot}: {f_name}")
            dl_path = await target_media_msg.download_media(file=f"temp_{f_name}")
            await client.disconnect()
            return dl_path

    await client.disconnect()
    return None

# --- ENSURE COMPATIBLE H.264/AAC FOR TV PICTURE ---
def transcode_and_upload(source_file, title_label):
    safe_title = re.sub(r'[^a-zA-Z0-9_-]', '_', title_label)
    final_output = f"{safe_title}.mp4"
    
    print(f"\n[*] Verifying TV video compatibility for {source_file}...")
    
    # Highly optimized FFmpeg command to prevent GitHub Actions memory crashes
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-i", source_file,
        "-c:v", "libx264",           # Force standard H.264 video (The ONLY codec TV browsers like)
        "-preset", "ultrafast",      # Critical: Stops GitHub Actions from crashing
        "-crf", "28",                # Critical: Lowered from 24 to 28 to save RAM and disk space
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",   # Critical: Allows instant web streaming
        final_output
    ]
    
    # Run with capture_output to see exact errors if it fails
    proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    
    if proc.returncode == 0:
        print("[+] TV-Ready Transcode successful!")
        upload_target = final_output
    else:
        print(f"[-] FFMPEG TRANSCODE FAILED! GitHub Actions likely ran out of memory.")
        print(f"[-] Error Log: {proc.stderr[-500:]}") # Print the last 500 chars of the error
        print(f"[*] FALLBACK: Uploading raw incompatible file to Drive...")
        upload_target = source_file
    
    print(f"[*] Uploading {upload_target} to Google Drive...")
    service = get_drive_service()
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    metadata = {'name': os.path.basename(upload_target), 'parents': [folder_id]}
    media = MediaFileUpload(upload_target, mimetype='video/mp4', resumable=True)
    
    uploaded_file = service.files().create(body=metadata, media_body=media, fields='id').execute()
    file_id = uploaded_file.get('id')
    print(f"[+] Upload complete. File ID: {file_id}")
    
    # NEW: Automatically set Google Drive file to Public so the TV streams it without 403 errors
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"}
        ).execute()
        print("[+] File permissions successfully set to Public.")
    except Exception as e:
        print(f"[-] Could not set public permissions: {e}")
    
    # Cleanup files to free up GitHub Actions space
    for f in [source_file, final_output]:
        if os.path.exists(f):
            os.remove(f)
            
    print(f"[✓] Completed workflow for: {title_label}")

async def main():
    if not MOVIE_NAME:
        print("[-] Missing MOVIE_NAME parameter.")
        sys.exit(1)

    downloaded = try_filmyzilla_scrape(MOVIE_NAME)

    if not downloaded:
        downloaded = await try_telegram_bots(MOVIE_NAME)

    if downloaded:
        transcode_and_upload(downloaded, MOVIE_NAME)
        sync_movies_json()
    else:
        print(f"[-] All sources failed to retrieve '{MOVIE_NAME}'.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
