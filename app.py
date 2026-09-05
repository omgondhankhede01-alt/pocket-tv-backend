import os
import sys
import re
import shutil
import asyncio
import threading
from flask import Flask
from telethon import TelegramClient

# ==========================================
# CONFIGURATION
# ==========================================
API_ID = 32183183
API_HASH = "198b328ce18f36d0ee8e69f1256d7a16"
BOT_USERNAMES = ["@iPapkornA2bot", "@iPopcornMBot", "@kevinhartrobot"]
MOVIES_TXT = "movies_to_download.txt"

# Target Google Drive folder path mapped inside the cloud environment
DRIVE_FOLDER_PATH = "./PocketTV"
os.makedirs(DRIVE_FOLDER_PATH, exist_ok=True)

if not os.path.exists(MOVIES_TXT):
    with open(MOVIES_TXT, "w", encoding="utf-8") as f:
        f.write("Inception\nInterstellar\n")

# ==========================================
# FLASK WEB SERVER (To keep Render awake via UptimeRobot)
# ==========================================
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Pocket TV Downloader is active and running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# ==========================================
# SMART SCORING & FILTER ENGINE
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
# TELEGRAM DOWNLOAD PIPELINE
# ==========================================
async def download_worker():
    client = TelegramClient("session_render", API_ID, API_HASH)
    print("\n[TELEGRAM] Connecting to Telegram...")
    await client.start()
    print("[TELEGRAM] Connected successfully!\n")

    while True:
        if not os.path.exists(MOVIES_TXT):
            await asyncio.sleep(10)
            continue

        with open(MOVIES_TXT, "r", encoding="utf-8") as f:
            queries = [line.strip() for line in f if line.strip()]

        if not queries:
            await asyncio.sleep(15)
            continue

        query = queries[0] # Take the first movie in line
        print(f"\n==========================================")
        print(f"[ENGINE] Processing: {query}")
        print(f"==========================================\n")
        
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
                        print(f"[TELEGRAM] WARNING: Series file detected ({file_name}). Skipping...")
                        continue

                    print(f"[TELEGRAM] Downloading '{file_name}'...")
                    download_path = await msg.download_media(file=file_name)
                    
                    if os.path.exists(download_path) and os.path.getsize(download_path) < 10 * 1024 * 1024:
                        print(f"[ERROR] Downloaded file is too small. Rejecting...")
                        os.remove(download_path)
                        continue

                    dest_path = os.path.join(DRIVE_FOLDER_PATH, file_name)
                    shutil.move(download_path, dest_path)
                    print(f"🎉 [SUCCESS] Saved {file_name}!")
                    
                    # Remove downloaded movie from text file so it moves to the next one
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

def run_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(download_worker())

if __name__ == "__main__":
    # Start Telegram background loop in a separate thread
    t = threading.Thread(target=run_loop)
    t.start()
    
    # Start Flask web server on main thread
    run_web()