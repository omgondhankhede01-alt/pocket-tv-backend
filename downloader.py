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
        return f"{re.sub(r'\b(?:s|season)\s*\d{1,2}\s*(?:e|ep|episode)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip().title()} S{int(se_match.group(1)):02d}E{int(se_match.group(2)):02d}"
    elif es_match:
        return f"{re.sub(r'\b(?:e|ep|episode)\s*\d{1,2}\s*(?:s|season)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip().title()} S{int(es_match.group(2)):02d}E{int(es_match.group(1)):02d}"
    elif e_match:
        return f"{re.sub(r'\b(?:e|ep|episode)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip().title()} S01E{int(e_match.group(1)):02d}"
    else:
        return clean.title()

def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get("GCP_REFRESH_TOKEN", ""),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GCP_CLIENT_ID", ""),
        client_secret=os.environ.get("GCP_CLIENT_SECRET", "")
    )
    return build('drive', 'v3', credentials=creds)

def sync_movies_json():
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    service = get_drive_service()
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, pageSize=200, fields="files(id, name, webViewLink, webContentLink)").execute()
    
    movie_data = [{"id": f.get("id"), "name": f.get("name"), "webViewLink": f.get("webViewLink"), "webContentLink": f.get("webContentLink", "#")} for f in results.get('files', [])]
    with open("movies.json", "w", encoding="utf-8") as f:
        json.dump(movie_data, f, indent=4)
    print("[*] movies.json synced successfully.")

def install_browser_engine():
    """Dynamically installs Playwright so the script can act like a human user."""
    try:
        import playwright
    except ImportError:
        print("[*] Human-simulation engine not found. Installing headless browser...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        print("[+] Headless browser installed successfully!")

# --- SOURCE 1: FILMYZILLA SCRAPER (FAST STATIC) ---
def try_filmyzilla_scrape(query):
    print(f"\n[Source 1] Searching Filmyzilla for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    episode_match = re.search(r'\bE(\d{1,2})\b', query, re.IGNORECASE)
    base_title = re.sub(r'\bS\d{1,2}E\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    
    try:
        r = session.get(f"{FILMYZILLA_DOMAIN}/search/{quote(base_title)}.html", timeout=12)
        if r.status_code != 200: return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    candidates = [(10, a.get_text(strip=True), urljoin(FILMYZILLA_DOMAIN, a['href'])) for a in soup.find_all("a", href=True) if any(seg in a['href'] for seg in ["/movie/", "/series/"]) and clean_text(base_title) in clean_text(a.get_text())]

    if not candidates: return None
    target_page = candidates[0][2]
    
    try:
        r2 = session.get(target_page, timeout=12)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        tier_candidates = [(a.get_text(strip=True), urljoin(FILMYZILLA_DOMAIN, a['href'])) for a in soup2.find_all("a", href=True) if "/server/" in a['href']]

        if not tier_candidates: return None

        selected_tier = tier_candidates[0]
        if episode_match:
            ep_num = int(episode_match.group(1))
            for text, link in tier_candidates:
                if re.search(rf'\b(ep|episode|e)[-_\s]*0?{ep_num}\b', text, re.IGNORECASE):
                    selected_tier = (text, link)
                    break

        r3 = session.get(selected_tier[1], timeout=12)
        soup3 = BeautifulSoup(r3.text, "html.parser")
        server_links = [urljoin(FILMYZILLA_DOMAIN, a['href']) for a in soup3.find_all("a", href=True) if "/verified/" in a['href']]
        
        if not server_links: return None

        res = session.get(server_links[0], stream=True, allow_redirects=True, timeout=15)
        if res.status_code == 200:
            local_dl = "temp_raw_stream.mkv"
            with open(local_dl, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)
            return local_dl
    except Exception:
        pass
    return None

# --- SOURCE 2: ANIMAHd SCRAPER (HUMAN BROWSER SIMULATION) ---
async def try_animahd_scrape(query):
    print(f"\n[Source 2] Searching AnimaHD for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    episode_match = re.search(r'\b(?:e|ep|episode)\s*(\d{1,2})\b', query, re.IGNORECASE)
    if not episode_match:
        episode_match = re.search(r'\bs\d{1,2}e(\d{1,2})\b', query, re.IGNORECASE)
    
    base_title = re.sub(r'\b(?:s|season)\s*\d{1,2}\s*(?:e|ep|episode)\s*\d{1,2}\b|\b(?:e|ep|episode)\s*\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    
    try:
        r = session.get(f"{ANIMAHD_DOMAIN}/?s={quote(base_title)}", timeout=12)
        if r.status_code != 200: return None
        soup = BeautifulSoup(r.text, "html.parser")
        
        anime_page_url = next((a['href'] for a in soup.find_all("a", href=True) if clean_text(base_title) in clean_text(a.get_text()) and ANIMAHD_DOMAIN in a['href']), None)
        if not anime_page_url:
            anime_page_url = next((a['href'] for a in soup.find_all("a", href=True) if ANIMAHD_DOMAIN in a['href'] and a['href'] != ANIMAHD_DOMAIN + "/"), None)
            
        if not anime_page_url:
            print("[-] Series page not found on AnimaHD.")
            return None

        print(f"[*] Found Series Page: {anime_page_url}")
        r2 = session.get(anime_page_url, timeout=12)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        
        episode_links = [(a.get_text(strip=True).lower(), urljoin(anime_page_url, a['href'])) for a in soup2.find_all("a", href=True) if re.search(r'(?:e|ep|episode|s\d+e)\s*\d+', a.get_text(strip=True).lower()) or ".mkv" in a.get_text(strip=True).lower() or ".mp4" in a.get_text(strip=True).lower()]
                
        target_ep_link = None
        ep_num = int(episode_match.group(1)) if episode_match else 1
        for text, link in episode_links:
            if re.search(rf'\b(?:e|ep|episode)\s*0?{ep_num}\b', text) or re.search(rf's\d+e0?{ep_num}\b', text):
                target_ep_link = link
                break
                
        if not target_ep_link and episode_links:
            target_ep_link = episode_links[0][1]
            
        if not target_ep_link:
            print("[-] Episode link not found on series page.")
            return None
            
        print(f"[*] Dispatching headless browser to bypass anti-bot gateway...")
        install_browser_engine()
        from playwright.async_api import async_playwright
        
        download_url = None
        final_url = None
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = await browser.new_context(user_agent=HEADERS["User-Agent"])
            page = await context.new_page()
            
            # Block images and fonts so the browser runs extremely fast
            await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font"] else route.continue_())

            print(f"[*] Browser loading Player Link: {target_ep_link}")
            try:
                await page.goto(target_ep_link, timeout=30000)
            except Exception:
                pass
                
            print("[*] Waiting for JS timers and auto-redirects...")
            try:
                await page.wait_for_url(re.compile(r"animesuki\.online|gate=|animahd\.online"), timeout=15000)
            except Exception:
                pass
                
            if "animesuki" in page.url or "gate=" in page.url:
                print(f"[+] Reached Gateway: {page.url}")
                try:
                    continue_btn = page.locator("text=Continue")
                    await continue_btn.wait_for(state="visible", timeout=15000)
                    await continue_btn.click()
                    print("[+] Browser clicked 'Continue'")
                    
                    dest_btn = page.locator("text=Go To Destination")
                    await dest_btn.wait_for(state="visible", timeout=15000)
                    await dest_btn.click()
                    print("[+] Browser clicked 'Go To Destination'")
                    
                    print("[*] Waiting for final streaming host...")
                    await page.wait_for_url(re.compile(r"animahd\.online|eid="), timeout=20000)
                except Exception as e:
                    print(f"[-] Browser click error: {e}")
            
            final_url = page.url
            print(f"[+] Final Reached Host: {final_url}")
            
            if "eid=" in final_url and "passed=" not in final_url:
                final_url += "&passed=1" if "?" in final_url else "?passed=1"
                await page.goto(final_url)
                
            links = await page.query_selector_all("a")
            for link in links:
                text = (await link.inner_text()).lower()
                href = await link.get_attribute("href")
                if href and ("download" in text or "url?id=" in href or ".mkv" in href or ".mp4" in href):
                    download_url = urljoin(page.url, href)
                    break
                    
            await browser.close()
            
        if not download_url:
            print("[-] Could not locate final download button via browser.")
            return None
            
        print(f"[+] Extracting file directly from: {download_url}")
        local_dl = "temp_animahd_stream.mkv"
        session.headers.update({"Referer": final_url})
        with session.get(download_url, stream=True, allow_redirects=True, timeout=25) as res:
            res.raise_for_status()
            with open(local_dl, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)
                    
        return local_dl

    except Exception as e:
        print(f"[-] AnimaHD browser scraper error: {e}")
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
        print(f"[-] Telegram bots fallback skipped: {e}")
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

    # Note: Filmyzilla is completely static and fast, so we keep it sync
    downloaded = try_filmyzilla_scrape(MOVIE_NAME)
    if not downloaded:
        # AnimaHD now triggers the async human-browser simulation
        downloaded = await try_animahd_scrape(MOVIE_NAME)
    if not downloaded:
        downloaded = await try_telegram_bots(MOVIE_NAME)

    if downloaded:
        transcode_and_upload(downloaded, MOVIE_NAME)
        sync_movies_json()
    else:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
