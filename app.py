import os
import re
import shutil
import asyncio
import threading
import json
from flask import Flask, render_template_string
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# CONFIGURATION
# ==========================================
API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
SESSION_STRING = os.environ.get("TG_SESSION", "")
BOT_USERNAMES = ["@iPapkornA2bot", "@iPopcornMBot", "@kevinhartrobot"]
MOVIES_TXT = "movies_to_download.txt"

DRIVE_FOLDER_PATH = "./PocketTV"
os.makedirs(DRIVE_FOLDER_PATH, exist_ok=True)

if not os.path.exists(MOVIES_TXT):
    with open(MOVIES_TXT, "w", encoding="utf-8") as f:
        f.write("Inception\nInterstellar\n")

# ==========================================
# FLASK WEB SERVER & UI
# ==========================================
web_app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pocket TV - Cloud Library</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --accent-color: #38bdf8;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 15px;
            margin-bottom: 25px;
        }
        h1 {
            margin: 0;
            font-size: 24px;
            color: var(--accent-color);
        }
        .status-badge {
            background-color: #065f46;
            color: #6ee7b7;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 14px;
            border: 1px solid #047857;
        }
        .file-grid {
            display: grid;
            gap: 15px;
        }
        .file-card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: border-color 0.2s ease;
        }
        .file-card:hover {
            border-color: var(--accent-color);
        }
        .file-info {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        .file-name {
            font-size: 16px;
            font-weight: 600;
        }
        .file-meta {
            font-size: 13px;
            color: var(--text-muted);
        }
        .actions {
            display: flex;
            gap: 10px;
        }
        .btn {
            background-color: var(--accent-color);
            color: #0f172a;
            padding: 8px 16px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 600;
            font-size: 14px;
            transition: opacity 0.2s;
        }
        .btn:hover {
            opacity: 0.9;
        }
        .empty-state {
            text-align: center;
            padding: 40px;
            color: var(--text-muted);
            border: 2px dashed var(--border-color);
            border-radius: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Pocket TV Library</h1>
            <div class="status-badge">🟢 Cloud Live & Synced</div>
        </header>

        <div class="file-grid">
            {% if files %}
                {% for file in files %}
                <div class="file-card">
                    <div class="file-info">
                        <span class="file-name">🎬 {{ file.name }}</span>
                        <span class="file-meta">Google Drive Cloud Storage</span>
                    </div>
                    <div class="actions">
                        <a href="{{ file.webViewLink }}" target="_blank" class="btn">Open / View</a>
                        {% if file.webContentLink %}
                        <a href="{{ file.webContentLink }}" class="btn" style="background-color: #10b981; color: white;">Download</a>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <h3>No movies found in your Drive folder yet!</h3>
                    <p>Add movie names to your <code>movies_to_download.txt</code> queue, and they will automatically appear here once uploaded.</p>
                </div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

def get_drive_files():
    creds_json = os.environ.get("GCP_CREDENTIALS", "")
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    
    if not creds_json or not folder_id:
        return []

    try:
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        query = f"'{folder_id}' in parents and trashed = false"
        results = service.files().list(
            q=query, 
            pageSize=100, 
            fields="files(id, name, webViewLink, webContentLink, size)"
        ).execute()
        
        return results.get('files', [])
    except Exception as e:
        print(f"[DRIVE LIST ERROR] {e}")
        return []

@web_app.route('/')
def home():
    files = get_drive_files()
    return render_template_string(HTML_TEMPLATE, files=files)

def run_web():
    port = int(os.environ.get("PORT", 10000))
    print(f"[WEB] Starting Flask server on port {port}...")
    web_app.run(host="0.0.0.0", port=port)

# ==========================================
# GOOGLE DRIVE UPLOAD HELPER
# ==========================================
def upload_to_drive(file_path):
    creds_json = os.environ.get("GCP_CREDENTIALS", "")
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    
    if not creds_json or not folder_id:
        print("[DRIVE ERROR] GCP_CREDENTIALS or DRIVE_FOLDER_ID missing! Skipping upload.")
        return False

    try:
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        file_name = os.path.basename(file_path)
        print(f"[DRIVE] Uploading '{file_name}' to Google Drive...")

        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        media = MediaFileUpload(file_path, resumable=True)
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        print(f"☁️ [SUCCESS] Uploaded to Google Drive! File ID: {file.get('id')}")
        
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"[CLEANUP] Deleted local file: {file_name}")
            
        return True
    except Exception as e:
        print(f"[DRIVE ERROR] Failed to upload {file_path}: {e}")
        return False

# ==========================================
# SMART SCORING ENGINE
# ==========================================
def get_best_option(options_list, movie_title=""):
    best_score = -9999
    best_opt = None
    clean_title = re.sub(r'[^\w\s]', '', str(movie_title).lower())
    
    for text, opt in options_list:
        t = str(text).lower()
        score = 0
        
        if re.search(r'\bs\d{1,2}\s?e\d{1,2}\b', t) or re.search(r'\bseason\s?\d+\b', t) or re.search(r'\bepisode\s?\d+\b', t):
            score -= 5000
            
        clean_text = re.sub(r'[^\w\s]', '', t)
        if clean_title and clean_title in clean_text:
            score += 500
            
        if 'hindi' in t or 'hin' in t: 
            score += 1000
            
        if '1080' in t: score += 100
        elif '720' in t: score += 70
        elif '480' in t: score += 40
        else: score += 5 
        
        if score > best_score:
            best_score = score
            best_opt = opt
            
    return best_opt

# ==========================================
# TELEGRAM DOWNLOAD WORKER
# ==========================================
async def download_worker():
    if not SESSION_STRING:
        print("[ERROR] TG_SESSION environment variable is missing! App cannot log into Telegram.")
        return

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    print("\n[TELEGRAM] Connecting to Telegram using Session String...")
    await client.connect()
    
    if not await client.is_user_authorized():
        print("[ERROR] Session string is invalid or expired. Please regenerate your session string.")
        return
        
    print("[TELEGRAM] Connected and authorized successfully!\n")

    while True:
        try:
            if not os.path.exists(MOVIES_TXT):
                await asyncio.sleep(10)
                continue

            with open(MOVIES_TXT, "r", encoding="utf-8") as f:
                queries = [line.strip() for line in f if line.strip()]

            if not queries:
                await asyncio.sleep(15)
                continue

            query = queries[0]
            print(f"\n[ENGINE] Processing: {query}")
            
            success = False
            for target_bot in BOT_USERNAMES:
                if success: break
                
                print(f"[TELEGRAM] Trying bot: {target_bot}")
                try:
                    await client.send_message(target_bot, "/start")
                    await asyncio.sleep(2) 
                    
                    async with client.conversation(target_bot, timeout=90) as conv:
                        await conv.send_message(query)
                        await asyncio.sleep(1)
                        
                        if target_bot == "@iPopcornMBot":
                            try:
                                await conv.get_response(timeout=3)
                            except asyncio.TimeoutError:
                                pass
                            await conv.send_message("/start")
                            await asyncio.sleep(2)
                            
                        res = await conv.get_response()
                        
                        for step in range(3):
                            if res.media: break 
                                
                            starts = re.findall(r'(/start\s+[a-zA-Z0-9_-]+)', res.raw_text)
                            if starts:
                                options = []
                                for line in res.raw_text.split('\n'):
                                    match = re.search(r'(/start\s+[a-zA-Z0-9_-]+)', line)
                                    if match: options.append((line, match.group(1)))
                                
                                chosen_start = get_best_option(options, query) or starts[0]
                                await conv.send_message(chosen_start)
                                await asyncio.sleep(2)
                                res = await conv.get_response()
                                continue
                                
                            if res.buttons:
                                all_btns = [btn for row in res.buttons for btn in row]
                                options = [(btn.text, btn) for btn in all_btns if btn.text]
                                target = get_best_option(options, query) or (all_btns[0] if all_btns else None)
                                
                                if target:
                                    if target.url and "start=" in target.url:
                                        payload = target.url.split("start=")[-1].split("&")[0]
                                        await conv.send_message(f"/start {payload}")
                                    else:
                                        await target.click()
                                    await asyncio.sleep(2)
                                    res = await conv.get_response()
                                    continue
                            break
                        
                        msg = res if res.media else None
                        if not msg:
                            for _ in range(5):
                                try:
                                    follow = await conv.get_response(timeout=4)
                                    if follow.media:
                                        msg = follow
                                        break
                                except asyncio.TimeoutError:
                                    break
                        
                        if not msg or not msg.media:
                            print(f"[TELEGRAM] {target_bot} returned no valid media. Moving to next bot...")
                            continue 
                        
                        file_name = msg.file.name or f"{query}.mp4"
                        
                        if re.search(r'\bs\d{1,2}\s?e\d{1,2}\b', file_name.lower()):
                            print(f"[TELEGRAM] WARNING: Series file detected. Skipping...")
                            continue

                        print(f"[TELEGRAM] Downloading '{file_name}'...")
                        download_path = await msg.download_media(file=file_name)
                        
                        if os.path.exists(download_path) and os.path.getsize(download_path) < 10 * 1024 * 1024:
                            print(f"[ERROR] Downloaded file is too small. Rejecting...")
                            os.remove(download_path)
                            continue

                        # Upload straight to Google Drive & cleanup local storage
                        upload_success = upload_to_drive(download_path)
                        
                        if upload_success:
                            remaining = queries[1:]
                            with open(MOVIES_TXT, "w", encoding="utf-8") as f:
                                f.write("\n".join(remaining) + "\n")
                                success = True
                        
                except Exception as e:
                    print(f"[ERROR with {target_bot}] {e}")
            
            if not success:
                print(f"❌ [ERROR] All bots failed for '{query}'. Moving past it.")
                remaining = queries[1:]
                with open(MOVIES_TXT, "w", encoding="utf-8") as f:
                    f.write("\n".join(remaining) + "\n")
                    
            await asyncio.sleep(10)
        except Exception as loop_error:
            print(f"[WORKER ERROR] {loop_error}")
            await asyncio.sleep(10)

def run_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(download_worker())

if __name__ == "__main__":
    t = threading.Thread(target=run_loop)
    t.daemon = True
    t.start()
    
    run_web()
