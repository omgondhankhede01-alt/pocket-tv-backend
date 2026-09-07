import os
import sys
import re
import json
import time
import asyncio
import subprocess
import requests
import unicodedata
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

# Verified bots
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

def sanitize_title(title):
    """
    1. Converts accented characters (e.g. Pokémon -> Pokemon)
    2. Standardizes TV episodes into 'Series Name S01E01' format
    """
    clean = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('utf-8')
    
    se_match = re.search(r'\b(?:s|season)\s*(\d{1,2})\s*(?:e|ep|episode)\s*(\d{1,2})\b', clean, re.IGNORECASE)
    es_match = re.search(r'\b(?:e|ep|episode)\s*(\d{1,2})\s*(?:s|season)\s*(\d{1,2})\b', clean, re.IGNORECASE)
    e_match = re.search(r'\b(?:e|ep|episode)\s*(\d{1,2})\b', clean, re.IGNORECASE)
    
    if se_match:
        s_num = int(se_match.group(1))
        e_num = int(se_match.group(2))
        base = re.sub(r'\b(?:s|season)\s*\d{1,2}\s*(?:e|ep|episode)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip()
        base = re.sub(r'[<>:"/\\|?*]', '', base).strip()
        return f"{base.title()} S{s_num:02d}E{e_num:02d}"
    elif es_match:
        e_num = int(es_match.group(1))
        s_num = int(es_match.group(2))
        base = re.sub(r'\b(?:e|ep|episode)\s*\d{1,2}\s*(?:s|season)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip()
        base = re.sub(r'[<>:"/\\|?*]', '', base).strip()
        return f"{base.title()} S{s_num:02d}E{e_num:02d}"
    elif e_match:
        e_num = int(e_match.group(1))
        base = re.sub(r'\b(?:e|ep|episode)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip()
        base = re.sub(r'[<>:"/\\|?*]', '', base).strip()
        return f"{base.title()} S01E{e_num:02d}"
    else:
        clean = re.sub(r'[<>:"/\\|?*]', '', clean).strip()
        return clean.title()

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

# --- SOURCE 2: ANIMAHd SCRAPER (DEEP LINK HUNTER) ---
def try_animahd_scrape(query):
    print(f"\n[Source 2] Searching AnimaHD for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    episode_match = re.search(r'\bE(\d{1,2})\b', query, re.IGNORECASE)
    base_title = re.sub(r'\bS\d{1,2}E\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    
    search_url = f"https://animahd.com/?s={quote(base_title)}"
    
    try:
        r = session.get(search_url, timeout=12)
        if r.status_code != 200: return None
        soup = BeautifulSoup(r.text, "html.parser")
        
        search_results = soup.find_all("a", href=True)
        anime_page_url = None
        for a in search_results:
            if clean_text(base_title) in clean_text(a.text):
                anime_page_url = a['href']
                break
                
        if not anime_page_url:
            print("[-] No matching titles found on AnimaHD search.")
            return None

        print(f"[*] Found Anime Page: {anime_page_url}")
        r2 = session.get(anime_page_url, timeout=12)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        
        episode_links = []
        for a in soup2.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a['href']
            if any(k in text.lower() for k in ["s0", "e0", "episode", "ep"]) or ".mkv" in href or ".mp4" in href:
                episode_links.append((text, href))
                
        if not episode_links:
            print("[-] Could not find episode links on the anime page.")
            return None

        target_ep_link = episode_links[0][1] 
        if episode_match:
            ep_num = int(episode_match.group(1))
            ep_pattern = re.compile(rf'\b(ep|episode|e)[-_\s]*0?{ep_num}\b', re.IGNORECASE)
            for text, link in episode_links:
                if ep_pattern.search(text) or f"ep {ep_num}" in text.lower():
                    target_ep_link = link
                    break
        
        print(f"[*] Scanning Episode Page links: {target_ep_link}")
        
        r3 = session.get(target_ep_link, timeout=12)
        soup3 = BeautifulSoup(r3.text, "html.parser")
        
        # Collect all anchor links on the episode page for deep inspection
        all_links = [(a.get_text(strip=True), a['href']) for a in soup3.find_all("a", href=True)]
        
        video_stream_url = None
        for text, href in all_links:
            t_lower = text.lower()
            h_lower = href.lower()
            if "download" in t_lower or "drive.google.com" in h_lower or ".mkv" in h_lower or ".mp4" in h_lower or "server" in t_lower or "player" in t_lower:
                if href.startswith("http"):
                    video_stream_url = href
                    print(f"[+] Matched stream link via '{text}': {href}")
                    break
                elif "drive.google.com" in h_lower or "file" in h_lower:
                    video_stream_url = urljoin(target_ep_link, href)
                    print(f"[+] Matched relative link via '{text}': {video_stream_url}")
                    break
                
        if not video_stream_url:
            iframe = soup3.find("iframe")
            if iframe and 'src' in iframe.attrs:
                video_stream_url = iframe['src']
                print(f"[+] Found iframe stream source: {video_stream_url}")
                
        if not video_stream_url:
            print("[-] Diagnostic - Could not match a download link. Here are all links found on the episode page:")
            for text, href in all_links[:20]:
                print(f"    - Text: '{text}' | Href: '{href}'")
            return None
            
        print(f"[+] Downloading media stream from: {video_stream_url}")
        
        local_dl = "temp_animahd_stream.mkv"
        with session.get(video_stream_url, stream=True, allow_redirects=True, timeout=15) as res:
            res.raise_for_status()
            with open(local_dl, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
                        
        return local_dl

    except Exception as e:
        print(f"[-] Error scraping AnimaHD: {e}")
        return None

# --- SOURCE 3: TELEGRAM BOTS FALLBACK ---
async def try_telegram_bots(query):
    print(f"\n[Source 3] Falling back to Telegram bots for: '{query}'...")
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
    clean_title = sanitize_title(title_label)
    final_output = f"{clean_title}.mp4"
    
    print(f"\n[*] Standardized Title: {clean_title}")
    print(f"[*] Verifying TV video compatibility for {source_file}...")
    
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-i", source_file,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        final_output
    ]
    
    proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    
    if proc.returncode == 0:
        print("[+] TV-Ready Transcode successful!")
        upload_target = final_output
    else:
        print(f"[-] FFMPEG TRANSCODE FAILED! GitHub Actions ran low on resources.")
        print(f"[-] Error Log: {proc.stderr[-500:]}")
        print(f"[*] FALLBACK: Uploading raw file to Drive...")
        upload_target = source_file
    
    print(f"[*] Uploading {upload_target} to Google Drive...")
    service = get_drive_service()
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    metadata = {'name': clean_title + ".mp4", 'parents': [folder_id]}
    media = MediaFileUpload(upload_target, mimetype='video/mp4', resumable=True)
    
    uploaded_file = service.files().create(body=metadata, media_body=media, fields='id').execute()
    file_id = uploaded_file.get('id')
    print(f"[+] Upload complete. File ID: {file_id}")
    
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"}
        ).execute()
        print("[+] File permissions set to Public.")
    except Exception as e:
        print(f"[-] Could not set public permissions: {e}")
    
    for f in [source_file, final_output]:
        if os.path.exists(f):
            os.remove(f)
            
    print(f"[✓] Completed workflow for: {clean_title}")

async def main():
    if not MOVIE_NAME:
        print("[-] Missing MOVIE_NAME parameter.")
        sys.exit(1)

    # 1. Try Filmyzilla First
    downloaded = try_filmyzilla_scrape(MOVIE_NAME)

    # 2. Try AnimaHD
    if not downloaded:
        downloaded = try_animahd_scrape(MOVIE_NAME)

    # 3. Try Telegram Bots
    if not downloaded:
        downloaded = await try_telegram_bots(MOVIE_NAME)

    # Process and Upload
    if downloaded:
        transcode_and_upload(downloaded, MOVIE_NAME)
        sync_movies_json()
    else:
        print(f"[-] All sources failed to retrieve '{MOVIE_NAME}'.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
