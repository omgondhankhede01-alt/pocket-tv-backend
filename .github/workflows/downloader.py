import os
import sys
import re
import json
import time
import asyncio
import subprocess
import requests
import base64
import unicodedata
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote, parse_qs, urlparse
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
SESSION_STRING = os.environ.get("TG_SESSION", "")
MOVIE_NAME = os.environ.get("MOVIE_NAME", "").strip()

ACTIVE_BOTS = [
    "@kevinhartrobot",
    "@iPapkornA2bot"
]

FILMYZILLA_DOMAIN = "https://www.filmyzilla65.com"
ANIMAHD_DOMAIN = "https://animahd.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def clean_text(text):
    return re.sub(r'[^a-zA-Z0-9\s]', '', text).lower().strip()

def sanitize_title(title):
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

def decode_base64_string(encoded_str):
    try:
        encoded_str += "=" * ((-len(encoded_str)) % 4)
        return base64.b64decode(encoded_str).decode('utf-8')
    except Exception:
        return None

def resolve_gateway_url(url, session):
    """Deeply parses JS redirects, base64 p= parameters, and gateway targets"""
    current_url = url
    for _ in range(6):
        parsed = urlparse(current_url)
        query_params = parse_qs(parsed.query)
        
        # 1. Handle base64 'p=' parameters
        if "p=" in query_params:
            try:
                p_part = query_params["p"][0].split("&")[0]
                decoded = decode_base64_string(p_part)
                if decoded:
                    current_url = decoded
                    continue
            except Exception:
                pass
                
        # 2. Handle base64 'target=' parameters
        if "target=" in query_params:
            try:
                target_part = query_params["target"][0].split("&")[0]
                decoded = decode_base64_string(target_part)
                if decoded:
                    current_url = decoded
                    continue
            except Exception:
                pass
                
        # 3. Fetch page and extract JS/HTML redirect targets if stuck on a gateway or player page
        try:
            r = session.get(current_url, timeout=12, allow_redirects=True)
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Search script tags for window.location or redirection links
            redirect_found = False
            for script in soup.find_all("script"):
                script_text = script.string or ""
                matches = re.findall(r'https?://[^\s\'"]+', script_text)
                for candidate in matches:
                    if "animesuki.online" in candidate or "animahd.online" in candidate or "target=" in candidate or "sec_route=1" in candidate:
                        current_url = candidate
                        redirect_found = True
                        break
                if redirect_found:
                    break
                    
            if redirect_found:
                continue
                
            # Search anchor links on gateway interstitial pages (like "Continue" or "Go To Destination")
            for a in soup.find_all("a", href=True):
                href = a['href']
                if "animesuki.online" in href or "target=" in href or "sec_route=1" in href or "passed=1" in href:
                    current_url = urljoin(r.url, href)
                    redirect_found = True
                    break
                    
            if redirect_found:
                continue
                
        except Exception as e:
            print(f"[-] Gateway loop error: {e}")
            
        break
    return current_url

# --- SOURCE 1: FILMYZILLA SCRAPER ---
def try_filmyzilla_scrape(query):
    print(f"\n[Source 1] Searching Filmyzilla for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    season_match = re.search(r'\bS(\d{1,2})\b', query, re.IGNORECASE)
    episode_match = re.search(r'\bE(\d{1,2})\b', query, re.IGNORECASE)
    base_title = re.sub(r'\bS\d{1,2}E\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    
    search_url = f"{FILMYZILLA_DOMAIN}/search/{quote(base_title)}.html"
    try:
        r = session.get(search_url, timeout=12)
        if r.status_code != 200:
            return None
    except Exception as e:
        print(f"[-] Filmyzilla error: {e}")
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
            if season_match and f"s{int(season_match.group(1)):02d}" in c_title:
                score += 20
            if score > 0:
                candidates.append((score, title, urljoin(FILMYZILLA_DOMAIN, href)))

    if not candidates:
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
                tier_candidates.append((text, urljoin(FILMYZILLA_DOMAIN, href)))

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

        session.headers.update({"Referer": target_page})
        r3 = session.get(selected_tier[1], timeout=12)
        soup3 = BeautifulSoup(r3.text, "html.parser")
        server_links = [urljoin(FILMYZILLA_DOMAIN, a['href']) for a in soup3.find_all("a", href=True) if "/verified/" in a['href']]
        
        if not server_links:
            return None

        session.headers.update({"Referer": selected_tier[1]})
        res = session.get(server_links[0], stream=True, allow_redirects=True, timeout=15)
        if res.status_code == 200:
            local_dl = "temp_raw_stream.mkv"
            with open(local_dl, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
            return local_dl
    except Exception as e:
        print(f"[-] Filmyzilla download error: {e}")
        return None

    return None

# --- SOURCE 2: ANIMAHd SCRAPER ---
def try_animahd_scrape(query):
    print(f"\n[Source 2] Searching AnimaHD for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    episode_match = re.search(r'\b(?:e|ep|episode)\s*(\d{1,2})\b', query, re.IGNORECASE)
    if not episode_match:
        episode_match = re.search(r'\bs\d{1,2}e(\d{1,2})\b', query, re.IGNORECASE)
    
    base_title = re.sub(r'\b(?:s|season)\s*\d{1,2}\s*(?:e|ep|episode)\s*\d{1,2}\b|\b(?:e|ep|episode)\s*\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    
    search_url = f"{ANIMAHD_DOMAIN}/?s={quote(base_title)}"
    
    try:
        r = session.get(search_url, timeout=12)
        if r.status_code != 200: return None
        soup = BeautifulSoup(r.text, "html.parser")
        
        anime_page_url = None
        for a in soup.find_all("a", href=True):
            if clean_text(base_title) in clean_text(a.get_text()) and ANIMAHD_DOMAIN in a['href']:
                anime_page_url = a['href']
                break
                
        if not anime_page_url:
            for a in soup.find_all("a", href=True):
                if ANIMAHD_DOMAIN in a['href'] and a['href'] != ANIMAHD_DOMAIN + "/":
                    anime_page_url = a['href']
                    break
                
        if not anime_page_url:
            print("[-] Series page not found on AnimaHD.")
            return None

        print(f"[*] Found Series Page: {anime_page_url}")
        r2 = session.get(anime_page_url, timeout=12)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        
        episode_links = []
        for a in soup2.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a['href']
            if any(k in text.lower() for k in ["ep", "episode", "e0", "s0", "watch"]) or any(k in href.lower() for k in ["ep", "episode", "watch", "player"]):
                episode_links.append((text, urljoin(anime_page_url, href)))
                
        target_ep_link = None
        if episode_match and episode_links:
            ep_num = int(episode_match.group(1))
            for text, link in episode_links:
                if re.search(rf'\b(ep|episode|e)\s*0?{ep_num}\b', text, re.IGNORECASE) or re.search(rf'e0?{ep_num}\b', link, re.IGNORECASE):
                    target_ep_link = link
                    break
                    
        if not target_ep_link and episode_links:
            target_ep_link = episode_links[0][1]
            
        if not target_ep_link:
            print("[-] Episode link not found.")
            return None
            
        # Resolve gateway loops using JS parser
        resolved_page = resolve_gateway_url(target_ep_link, session)
        if "?" in resolved_page:
            resolved_page += "&passed=1"
        else:
            resolved_page += "?passed=1"
            
        print(f"[*] Final Target Destination: {resolved_page}")
        
        r3 = session.get(resolved_page, timeout=15, allow_redirects=True)
        soup3 = BeautifulSoup(r3.text, "html.parser")
        
        download_url = None
        for a in soup3.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a['href']
            if "download" in text or "url?id=" in href or ".mkv" in href or ".mp4" in href:
                download_url = urljoin(r3.url, href)
                break
                
        if not download_url:
            print("[-] Download button not located on destination page.")
            return None
            
        print(f"[+] Downloading file from: {download_url}")
        local_dl = "temp_animahd_stream.mkv"
        session.headers.update({"Referer": r3.url})
        with session.get(download_url, stream=True, allow_redirects=True, timeout=20) as res:
            res.raise_for_status()
            with open(local_dl, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
                        
        return local_dl

    except Exception as e:
        print(f"[-] AnimaHD scraper error: {e}")
        return None

# --- SOURCE 3: TELEGRAM BOTS FALLBACK ---
async def try_telegram_bots(query):
    print(f"\n[Source 3] Falling back to Telegram bots for: '{query}'...")
    try:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        await client.connect()

        for bot in ACTIVE_BOTS:
            try:
                sent_msg = await client.send_message(bot, query)
                out_id = sent_msg.id
            except Exception:
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
                dl_path = await target_media_msg.download_media(file=f"temp_{f_name}")
                await client.disconnect()
                return dl_path

        await client.disconnect()
    except Exception as e:
        print(f"[-] Telegram bots fallback skipped due to network/auth limits: {e}")
    return None

def transcode_and_upload(source_file, title_label):
    clean_title = sanitize_title(title_label)
    final_output = f"{clean_title}.mp4"
    
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-i", source_file,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        final_output
    ]
    
    proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    upload_target = final_output if proc.returncode == 0 else source_file
    
    service = get_drive_service()
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    metadata = {'name': clean_title + ".mp4", 'parents': [folder_id]}
    media = MediaFileUpload(upload_target, mimetype='video/mp4', resumable=True)
    
    uploaded_file = service.files().create(body=metadata, media_body=media, fields='id').execute()
    file_id = uploaded_file.get('id')
    
    try:
        service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    except Exception:
        pass
    
    for f in [source_file, final_output]:
        if os.path.exists(f):
            os.remove(f)

async def main():
    if not MOVIE_NAME:
        sys.exit(1)

    downloaded = try_filmyzilla_scrape(MOVIE_NAME)
    if not downloaded:
        downloaded = try_animahd_scrape(MOVIE_NAME)
    if not downloaded:
        downloaded = await try_telegram_bots(MOVIE_NAME)

    if downloaded:
        transcode_and_upload(downloaded, MOVIE_NAME)
        sync_movies_json()
    else:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
