import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError
import firebase_admin
from firebase_admin import credentials, firestore

app = FastAPI()
# Firebase Init
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()
COLLECTION = "songs"

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


SONG_DB = {}  # song_id -> file_path

def generate_stable_id(file_path):
    hasher = hashlib.sha1()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()

def extract_metadata(file_path: str) -> dict:
    audio = MP3(file_path)
    duration = int(audio.info.length)

    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = {}

    def get(tag):
        try:
            return tags.get(tag).text[0]
        except:
            return None

    song_id = generate_stable_id(file_path)
    return {
        "id": song_id,
        "name": get("TIT2") or os.path.basename(file_path),
        "artist": get("TPE1"),
        "album": get("TALB"),
        "genre": get("TCON"),
        "imageUrl": None,
        "filePath": f"{BASE_URL}/stream/{quote(song_id)}",
        "duration": duration
    }

def scan_and_upload(base_dir="media"):
    print("📡 Scanning media directory...")
    base = Path(base_dir)
    songs_ref = db.collection(COLLECTION)

    for file in base.rglob("*.mp3"):
        file_path = str(file)
        try:
            song_id = generate_stable_id(file_path)

            # 🔍 Check if already in Firestore
            if songs_ref.document(song_id).get().exists:
                print(f"⏩ Skipping already uploaded: {file_path}")
                SONG_DB[song_id] = file_path  # still add to local cache!
                continue

            metadata = extract_metadata(file_path)
            SONG_DB[song_id] = file_path
            songs_ref.document(song_id).set(metadata)
            print(f"✅ Uploaded new: {metadata['name']}")

        except Exception as e:
            print(f"❌ Error processing {file_path}: {e}")

@app.get("/", response_model=dict)
async def root():
    return {"message": "Vibeify API is healthy!",
            "date": datetime.isoformat(datetime.today())}

@app.get("/stream/{song_id}")
def stream_song(song_id: str):
    path = SONG_DB.get(song_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Song not found")
    return FileResponse(path, media_type="audio/mpeg")

@app.on_event("startup")
def on_startup():
    scan_and_upload()

def start():
    scan_and_upload()
    """Launched with `poetry run start` at root level"""
    uvicorn.run("vibeify_backend.main:app", host="0.0.0.0", port=8000, reload=True)