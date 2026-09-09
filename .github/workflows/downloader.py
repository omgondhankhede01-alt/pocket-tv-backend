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
import socket
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote, quote_plus, urlparse, parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

socket.setdefaulttimeout(300)

API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
SESSION_STRING = os.environ.get("TG_SESSION", "")
MOVIE_NAME = os.environ.get("MOVIE_NAME", "").strip()
RUN_MODE = os.environ.get("RUN_MODE", "all").strip().lower()

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

def set_github_output(name, value):
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        try:
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(f"{name}={value}\n")
        except Exception:
            pass

def clean_text(text):
    return re.sub(r'[^a-zA-Z0-9\s]', '', text).lower().strip()

def sanitize_title(title):
    clean = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('utf-8')
    se_match = re.search(r'\b(?:s|season)\s*(\d{1,2})\s*(?:e|ep|episode)\s*(\d{1,2})\b', clean, re.IGNORECASE)
    es_match = re.search(r'\b(?:e|ep|episode)\s*(\d{1,2})\s*(?:s|season)\s*(\d{1,2})\b', clean, re.IGNORECASE)
    e_match = re.search(r'\b(?:e|ep|episode)\s*(\d{1,2})\b', clean, re.IGNORECASE)
    
    if se_match:
        base = re.sub(r'\b(?:s|season)\s*\d{1,2}\s*(?:e|ep|episode)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip()
        return f"{base.title()} S{int(se_match.group(1)):02d}E{int(se_match.group(2)):02d}"
    elif es_match:
        base = re.sub(r'\b(?:e|ep|episode)\s*\d{1,2}\s*(?:s|season)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip()
        return f"{base.title()} S{int(es_match.group(2)):02d}E{int(es_match.group(1)):02d}"
    elif e_match:
        base = re.sub(r'\b(?:e|ep|episode)\s*\d{1,2}\b', '', clean, flags=re.IGNORECASE).strip()
        return f"{base.title()} S01E{int(e_match.group(1)):02d}"
    else:
        return clean.title()

def get_quality_score(text):
    t = str(text).lower()
    if re.search(r'\b(1080p|1080|fhd|full\s*hd)\b', t):
        return 100
    if re.search(r'\b(720p|720)\b', t):
        return 90
    if re.search(r'\b(hd|high\s*def)\b', t):
        return 80
    if re.search(r'\b(2160p|4k|uhd)\b', t):
        return -50
    if re.search(r'\b(480p|360p|240p|sd|dvdrip|camrip|cam|ts)\b', t):
        return -100
    return 40

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
    results = service.files().list(q=query, pageSize=200, fields="files(id, name, webViewLink, webContentLink)").execute(num_retries=3)
    
    movie_data = [{"id": f.get("id"), "name": f.get("name"), "webViewLink": f.get("webViewLink"), "webContentLink": f.get("webContentLink", "#")} for f in results.get('files', [])]
    with open("movies.json", "w", encoding="utf-8") as f:
        json.dump(movie_data, f, indent=4)
    print("[*] movies.json synced successfully.")

def install_browser_engine():
    try:
        import playwright
    except ImportError:
        print("[*] Installing headless browser engine...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        print("[+] Headless browser ready.")

def decode_base64_string(encoded_str):
    try:
        encoded_str = encoded_str.replace('-', '+').replace('_', '/')
        encoded_str += "=" * ((4 - len(encoded_str) % 4) % 4)
        return base64.b64decode(encoded_str).decode('utf-8')
    except Exception:
        return None

# --- SOURCE 1: FILMYZILLA SCRAPER ---
def try_filmyzilla_scrape(query):
    print(f"\n[Source 1] Searching Filmyzilla for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    episode_match = re.search(r'\bE(\d{1,2})\b', query, re.IGNORECASE)
    base_title = re.sub(r'\bS\d{1,2}E\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    
    # Fix: Generate hyphenated slug to prevent Apache rewrite 404 errors
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', base_title).strip('-').lower()
    search_urls = [
        f"{FILMYZILLA_DOMAIN}/search/{slug}.html",
        f"{FILMYZILLA_DOMAIN}/search.php?q={quote_plus(base_title)}"
    ]
    
    soup = None
    for s_url in search_urls:
        try:
            r = session.get(s_url, timeout=12)
            if r.status_code == 200 and len(r.text) > 1000:
                soup = BeautifulSoup(r.text, "html.parser")
                break
        except Exception as e:
            print(f"[-] Failed reaching {s_url}: {e}")
            continue

    if not soup:
        print("[-] Filmyzilla search endpoints returned no valid response.")
        return None

    query_words = [w for w in clean_text(base_title).split() if len(w) > 2]
    candidates = []

    for a in soup.find_all("a", href=True):
        href = a['href']
        text = a.get_text(strip=True)
        clean_t = clean_text(text)
        
        if any(seg in href for seg in ["/movie/", "/files/", "/series/"]):
            # Keyword overlap matching ensures variations like 'The Dark Knight (2008)' match properly
            if all(qw in clean_t for qw in query_words):
                full_href = urljoin(FILMYZILLA_DOMAIN, href)
                score = get_quality_score(text)
                candidates.append((score, text, full_href))

    if not candidates:
        print(f"[-] No matching title candidates found on Filmyzilla for '{base_title}'.")
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    target_page = candidates[0][2]
    print(f"[+] Found candidate: '{candidates[0][1]}' -> {target_page}")
    
    try:
        r2 = session.get(target_page, timeout=12)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        tier_candidates = []

        for a in soup2.find_all("a", href=True):
            if any(k in a['href'] for k in ["/server/", "/file/", "/download/"]):
                href = urljoin(FILMYZILLA_DOMAIN, a['href'])
                txt = a.get_text(strip=True)
                score = get_quality_score(txt)
                tier_candidates.append((score, txt, href))

        if not tier_candidates:
            print("[-] No download tiers found on movie page.")
            return None

        selected_tier = None
        if episode_match:
            ep_num = int(episode_match.group(1))
            ep_matching = [t for t in tier_candidates if re.search(rf'\b(ep|episode|e)[-_\s]*0?{ep_num}\b', t[1], re.IGNORECASE)]
            if ep_matching:
                ep_matching.sort(key=lambda x: x[0], reverse=True)
                selected_tier = (ep_matching[0][1], ep_matching[0][2])
        
        if not selected_tier:
            tier_candidates.sort(key=lambda x: x[0], reverse=True)
            selected_tier = (tier_candidates[0][1], tier_candidates[0][2])

        print(f"[*] Filmyzilla selected tier: '{selected_tier[0]}'")

        r3 = session.get(selected_tier[1], timeout=12)
        soup3 = BeautifulSoup(r3.text, "html.parser")
        server_links = [
            urljoin(FILMYZILLA_DOMAIN, a['href']) 
            for a in soup3.find_all("a", href=True) 
            if any(k in a['href'] for k in ["/verified/", "/dload/", "download="])
        ]
        
        if not server_links:
            # Check if current page is already the final download redirect
            if "download" in r3.url or ".mp4" in r3.url or ".mkv" in r3.url:
                server_links = [r3.url]
            else:
                return None

        print(f"[+] Streaming from: {server_links[0]}")
        res = session.get(server_links[0], stream=True, allow_redirects=True, timeout=20)
        if res.status_code == 200:
            local_dl = "temp_raw_stream.mkv"
            with open(local_dl, "wb") as f:
                for chunk in res.iter_content(chunk_size=2*1024*1024):
                    if chunk: f.write(chunk)
            return local_dl
    except Exception as e:
        print(f"[-] Filmyzilla download error: {e}")
    return None

# --- SOURCE 2: ANIMAHd SCRAPER ---
async def try_animahd_scrape(query):
    print(f"\n[Source 2] Searching AnimaHD for: '{query}'...")
    session = requests.Session()
    session.headers.update(HEADERS)
    
    episode_match = re.search(r'\b(?:e|ep|episode)\s*(\d{1,2})\b', query, re.IGNORECASE)
    if not episode_match:
        episode_match = re.search(r'\bs\d{1,2}e(\d{1,2})\b', query, re.IGNORECASE)
    
    base_title = re.sub(r'\b(?:s|season)\s*\d{1,2}\s*(?:e|ep|episode)\s*\d{1,2}\b|\b(?:e|ep|episode)\s*\d{1,2}\b|\bS\d{1,2}\b|\bE\d{1,2}\b', '', query, flags=re.IGNORECASE).strip()
    clean_query = clean_text(base_title)

    try:
        r = session.get(f"{ANIMAHD_DOMAIN}/?s={quote(base_title)}", timeout=12)
        if r.status_code != 200: 
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        
        ignore_links = ["/filter/", "/anime-schedule/", "/dmca/", "/terms/", "/about/", "/contact/"]
        anime_page_url = None

        # STRICT CHECK ONLY: Eliminate the blind fallback that downloaded unrelated anime
        for a in soup.find_all("a", href=True):
            href = a['href']
            text = a.get_text()
            if ANIMAHD_DOMAIN in href and not any(ig in href.lower() for ig in ignore_links) and href.strip('/') != ANIMAHD_DOMAIN.strip('/'):
                if clean_query in clean_text(text) or clean_text(text) in clean_query:
                    anime_page_url = href
                    break
                    
        if not anime_page_url:
            print(f"[-] Not found on AnimaHD. Skipping (avoids grabbing incorrect anime)...")
            return None

        print(f"[*] Found Series Page: {anime_page_url}")
        r2 = session.get(anime_page_url, timeout=12)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        
        episode_links = []
        for a in soup2.find_all("a", href=True):
            txt = a.get_text(strip=True)
            t_low = txt.lower()
            if re.search(r'(?:e|ep|episode|s\d+e)\s*\d+', t_low) or ".mkv" in t_low or ".mp4" in t_low:
                href = urljoin(anime_page_url, a['href'])
                score = get_quality_score(txt)
                episode_links.append((score, txt, href))
                
        target_ep_link = None
        ep_num = int(episode_match.group(1)) if episode_match else 1
        ep_matches = [
            item for item in episode_links 
            if re.search(rf'\b(?:e|ep|episode)\s*0?{ep_num}\b', item[1], re.IGNORECASE) or re.search(rf's\d+e0?{ep_num}\b', item[1], re.IGNORECASE)
        ]
        
        if ep_matches:
            ep_matches.sort(key=lambda x: x[0], reverse=True)
            target_ep_link = ep_matches[0][2]
        elif episode_links:
            episode_links.sort(key=lambda x: x[0], reverse=True)
            target_ep_link = episode_links[0][2]
            
        if not target_ep_link:
            print("[-] Episode link not found on series page.")
            return None
            
        print(f"[*] Dispatching headless browser...")
        install_browser_engine()
        from playwright.async_api import async_playwright
        
        download_url = None
        final_url = None
        captured_urls = []
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = await browser.new_context(user_agent=HEADERS["User-Agent"], accept_downloads=True)
            page = await context.new_page()
            
            loop = asyncio.get_running_loop()
            download_future = loop.create_future()
            
            def _on_download(d):
                if not download_future.done():
                    download_future.set_result(d)
                    
            page.on("download", _on_download)
            context.on("page", lambda new_page: new_page.on("download", _on_download))
            
            await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font"] else route.continue_())
            page.on("request", lambda req: captured_urls.append(req.url))

            try:
                await page.goto(target_ep_link, timeout=30000)
                await page.wait_for_url(re.compile(r"animesuki\.online|target=|gate="), timeout=12000)
            except Exception:
                pass
                
            current_url = page.url
            if "target=" in current_url:
                parsed = urlparse(current_url)
                q_params = parse_qs(parsed.query)
                if "target" in q_params:
                    decoded = decode_base64_string(q_params["target"][0])
                    if decoded:
                        final_url = decoded
            
            if final_url:
                if "eid=" in final_url and "passed=" not in final_url:
                    final_url += "&passed=1" if "?" in final_url else "?passed=1"
                try:
                    await page.goto(final_url, timeout=20000)
                except Exception:
                    pass
            
            click_sequence = [r"Download Episode", r"Click Again to Continue", r"Final Step"]
            for step_regex in click_sequence:
                try:
                    btn = page.get_by_text(re.compile(step_regex, re.IGNORECASE)).first
                    if await btn.is_visible(timeout=6000):
                        await btn.click(force=True)
                        await page.wait_for_timeout(2000)
                except Exception:
                    pass
            
            local_dl = "temp_animahd_stream.mkv"
            try:
                download_obj = await asyncio.wait_for(asyncio.shield(download_future), timeout=12.0)
                await download_obj.save_as(local_dl)
                await browser.close()
                return local_dl
            except asyncio.TimeoutError:
                pass
            
            scored_captured = []
            for url in captured_urls:
                if re.search(r'\.mkv|\.mp4|workers\.dev|download=true', url, re.IGNORECASE):
                    if url != page.url and "latestanimeepisodes" not in url:
                        score = get_quality_score(url)
                        scored_captured.append((score, url))
                        
            if scored_captured:
                scored_captured.sort(key=lambda x: x[0], reverse=True)
                download_url = scored_captured[0][1]
                    
            if not download_url:
                links = await page.query_selector_all("a")
                found_links = []
                for link in links:
                    text = (await link.inner_text()).lower()
                    href = await link.get_attribute("href")
                    if href and ("download" in text or "url?id=" in href or ".mkv" in href or ".mp4" in href):
                        full_h = urljoin(page.url, href)
                        score = get_quality_score(f"{text} {full_h}")
                        found_links.append((score, full_h))
                if found_links:
                    found_links.sort(key=lambda x: x[0], reverse=True)
                    download_url = found_links[0][1]

            if download_url:
                try:
                    async with page.expect_download(timeout=90000) as dl_info:
                        await page.evaluate("url => window.location.href = url", download_url)
                    download = await dl_info.value
                    await download.save_as(local_dl)
                    await browser.close()
                    return local_dl
                except Exception:
                    pass

            await browser.close()
        return None

    except Exception as e:
        print(f"[-] AnimaHD error: {e}")
        return None

# --- SOURCE 3: TELEGRAM BOTS FALLBACK ---
async def try_telegram_bots(query):
    print(f"\n[Source 3] Engaging Telegram bots fallback for: '{query}'...")
    client = None
    try:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            print("[-] Telegram session is invalid or revoked. Please update TG_SESSION.")
            return None

        for bot in ACTIVE_BOTS:
            try:
                sent_msg = await client.send_message(bot, query)
                out_id = sent_msg.id
            except Exception:
                continue

            start_time = time.time()
            media_messages = []

            while time.time() - start_time < 25: 
                async for msg in client.iter_messages(bot, min_id=out_id, limit=15):
                    if (msg.media or msg.document or msg.video) and msg.id not in [m.id for m in media_messages]:
                        media_messages.append(msg)
                
                if media_messages:
                    best_current_score = max([get_quality_score(f"{getattr(m.file, 'name', '')} {m.text or ''}") for m in media_messages])
                    if best_current_score >= 90:
                        break
                        
                if len(media_messages) >= 3 and (time.time() - start_time > 8):
                    break
                await asyncio.sleep(1)

            if media_messages:
                scored_msgs = []
                for m in media_messages:
                    f_name = getattr(m.file, 'name', '') or ''
                    caption = m.text or ''
                    score = get_quality_score(f"{f_name} {caption}")
                    scored_msgs.append((score, m, f_name))

                scored_msgs.sort(key=lambda x: x[0], reverse=True)
                best_score, best_msg, best_fname = scored_msgs[0]
                print(f"[+] Selected Telegram media: '{best_fname}' (Quality Score: {best_score})")

                final_name = best_fname or "video.mp4"
                dl_path = await best_msg.download_media(file=f"temp_{final_name}")
                return dl_path

    except Exception as e:
        print(f"[-] Telegram bots error: {e}")
    finally:
        if client and client.is_connected():
            await client.disconnect()
            print("[*] Disconnected cleanly from Telegram session.")
    return None

def probe_file_streams(filepath):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,pix_fmt",
        "-of", "json", filepath
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(proc.stdout)
        video_codec = None
        pix_fmt = None
        audio_codec = None
        
        for s in info.get("streams", []):
            c_type = s.get("codec_type")
            if c_type == "video" and not video_codec:
                video_codec = s.get("codec_name", "").lower()
                pix_fmt = s.get("pix_fmt", "").lower()
            elif c_type == "audio" and not audio_codec:
                audio_codec = s.get("codec_name", "").lower()
                
        return video_codec, pix_fmt, audio_codec
    except Exception as e:
        print(f"[-] ffprobe inspection error: {e}")
        return None, None, None

def transcode_and_upload(source_file, title_label):
    clean_title = sanitize_title(title_label)
    final_output = f"{clean_title}.mp4"
    
    v_codec, pix_fmt, a_codec = probe_file_streams(source_file)
    print(f"[*] Detected streams: Video={v_codec} ({pix_fmt}), Audio={a_codec}")
    
    is_v_safe = (v_codec == "h264") and (pix_fmt == "yuv420p")
    is_a_safe = a_codec in ["aac", "mp3", "ac3"]
    
    if is_v_safe and is_a_safe:
        print("[+] Stream is fully TV-compliant. Remuxing instantaneously...")
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", source_file,
            "-c", "copy",
            "-movflags", "+faststart",
            final_output
        ]
    elif is_v_safe and not is_a_safe:
        print("[*] Remuxing video and transcoding audio to AAC...")
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", source_file,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            final_output
        ]
    else:
        print("[!] Re-encoding to universal 8-bit H.264 profile for legacy TV support...")
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", source_file,
            "-vf", "scale='min(1920,iw)':-2",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            final_output
        ]
    
    proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    upload_target = final_output if proc.returncode == 0 and os.path.exists(final_output) else source_file
    
    service = get_drive_service()
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    metadata = {'name': clean_title + ".mp4", 'parents': [folder_id]}
    
    print(f"[*] Uploading '{upload_target}' to Google Drive...")
    media = MediaFileUpload(upload_target, mimetype='video/mp4', resumable=True)
    
    uploaded_file = service.files().create(body=metadata, media_body=media, fields='id').execute(num_retries=5)
    file_id = uploaded_file.get('id')
    print(f"[+] Upload complete! File ID: {file_id}")
    
    try:
        service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute(num_retries=5)
    except Exception:
        pass
    
    for f in [source_file, final_output]:
        if os.path.exists(f):
            os.remove(f)

async def main():
    if not MOVIE_NAME:
        sys.exit(1)

    downloaded = None

    if RUN_MODE in ["web", "all"]:
        downloaded = try_filmyzilla_scrape(MOVIE_NAME)
        if not downloaded:
            downloaded = await try_animahd_scrape(MOVIE_NAME)

    if not downloaded and RUN_MODE in ["telegram", "all"]:
        downloaded = await try_telegram_bots(MOVIE_NAME)

    if downloaded:
        transcode_and_upload(downloaded, MOVIE_NAME)
        sync_movies_json()
        set_github_output("downloaded", "true")
        print("[+] Download and sync completed successfully.")
        sys.exit(0)
    else:
        set_github_output("downloaded", "false")
        if RUN_MODE == "web":
            print("[*] Web scrapers could not find the file. Handing off to sequential Telegram fallback...")
            sys.exit(0)
        else:
            print("[-] All sources exhausted. File could not be retrieved.")
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
