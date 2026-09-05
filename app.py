import os
import re
import shutil
import asyncio
import threading
import json
from flask import Flask, jsonify
from flask_cors import CORS  # <-- CORS Bypass added here
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
# FLASK WEB SERVER & API
# ==========================================
web_app = Flask(__name__)
CORS(web_app)  # <-- CORS Bypass activated here!

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
    return "Pocket TV Backend Engine is running and ready for Cloudflare!"

@web_app.route('/api/files')
def api_files():
    files = get_drive_files()
    file_list = []
    for f in files:
        file_list.append({
            "name": f.get("name"),
            "webViewLink": f.get("webViewLink"),
            "webContentLink": f.get("webContentLink", "#")
        })
    return jsonify(file_list)

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
        print("[ERROR] TG_SESSION environment variable is missing!")
        return

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("[ERROR] Session string is invalid.")
        return

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
            success = False
            
            for target_bot in BOT_USERNAMES:
                if success: break
                try:
                    await client.send_message(target_bot, "/start")
                    await asyncio.sleep(2) 
                    
                    async with client.conversation(target_bot, timeout=90) as conv:
                        await conv.send_message(query)
                        await asyncio.sleep(1)
                        
                        res = await conv.get_response()
                        
                        for step in range(3):
                            if res.media: break 
                                
                            starts = re.findall(r'(/start\s+[a-zA-Z0-9_-]+)', res.raw_text)
                            if starts:
                                options = [(line, re.search(r'(/start\s+[a-zA-Z0-9_-]+)', line).group(1)) for line in res.raw_text.split('\n') if re.search(r'(/start\s+[a-zA-Z0-9_-]+)', line)]
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
                            continue 
                        
                        file_name = msg.file.name or f"{query}.mp4"
                        if re.search(r'\bs\d{1,2}\s?e\d{1,2}\b', file_name.lower()):
                            continue

                        download_path = await msg.download_media(file=file_name)
                        if os.path.exists(download_path) and os.path.getsize(download_path) < 10 * 1024 * 1024:
                            os.remove(download_path)
                            continue

                        upload_success = upload_to_drive(download_path)
                        if upload_success:
                            remaining = queries[1:]
                            with open(MOVIES_TXT, "w", encoding="utf-8") as f:
                                f.write("\n".join(remaining) + "\n")
                                success = True
                        
                except Exception as e:
                    pass
            
            if not success:
                remaining = queries[1:]
                with open(MOVIES_TXT, "w", encoding="utf-8") as f:
                    f.write("\n".join(remaining) + "\n")
                    
            await asyncio.sleep(10)
        except Exception as loop_error:
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
