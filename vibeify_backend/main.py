import hashlib
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote




import uvicorn
import io
from fastapi import FastAPI, HTTPException, Response, UploadFile, File
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
FALLBACK_IMAGE_PATH = "assets/albumart.jpg"
PROFILE_PICTURES_DIR = "profile_pictures"

SONG_DB = {}  # song_id -> file_path
PFP_DB = {}  # user_id -> file_path

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
        "imageUrl": f"{BASE_URL}/cover/{quote(song_id)}",
        "filePath": f"{BASE_URL}/stream/{quote(song_id)}",
        "duration": duration
    }

@app.post("/upload/profile-picture/{user_id}")
async def upload_profile_picture(user_id: str, file: UploadFile = File(...)):
    """Upload a profile picture for a user"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    os.makedirs(PROFILE_PICTURES_DIR, exist_ok=True)
    extension = ".jpg"  
    if "png" in file.content_type:
        extension = ".png"
    file_path = os.path.join(PROFILE_PICTURES_DIR, f"{user_id}{extension}")
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer) 
        PFP_DB[user_id] = file_path
        return {
            "message": "Profile picture uploaded successfully",
            "user_id": user_id,
            "file_path": file_path,
            "image_url": f"{BASE_URL}/picture/{user_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")


def init_picture_db():
    """Initialize the profile picture database from existing files"""
    global PFP_DB
    PFP_DB = {}
    if not os.path.exists(PROFILE_PICTURES_DIR):
        return
    for file in Path(PROFILE_PICTURES_DIR).glob("*.*"):
        user_id = file.stem
        PFP_DB[user_id] = str(file)
        print(f"Loaded profile picture for {user_id}: {file}")


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
                print(f"⏩ Skipping already uploaded: {file_path} ({song_id})")
                SONG_DB[song_id] = file_path  # still add to local cache!
                continue

            metadata = extract_metadata(file_path)
            SONG_DB[song_id] = file_path
            songs_ref.document(song_id).set(metadata)
            print(f"✅ Uploaded new: {metadata['name']}")

        except Exception as e:
            print(f"❌ Error processing {file_path}: {e}")
    print( "📡 Media scan complete!")


@app.get("/", response_model=dict)
async def root():
    return {"message": "Vibeify API is healthy!",
            "date": datetime.isoformat(datetime.today())}


@app.get("/cover/{song_id}")
def get_cover(song_id: str):
    path = SONG_DB.get(song_id)
    if not path or not os.path.isfile(path):
        return _get_fallback_image()

    try:
        tags = ID3(path)
        apic = tags.get("APIC:")
        if apic:
            return Response(content=apic.data, media_type=apic.mime or "image/jpeg")
        else:
            # No cover art tag, return fallback image
            return _get_fallback_image()
    except Exception as e:
        print(f"Could not get image for {song_id}: {e}")
        return _get_fallback_image()
    
    
@app.get("/picture/{user_id}")
def get_profile_picture(user_id : str):
    path = PFP_DB.get(user_id)
    if not path or not os.path.isfile(path):
        return ""

    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg")


def _get_fallback_image():
    if not os.path.isfile(FALLBACK_IMAGE_PATH):
        raise HTTPException(status_code=500, detail="Fallback image not found")
    with open(FALLBACK_IMAGE_PATH, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg")

@app.get("/stream/{song_id}")
def stream_song(song_id: str):
    path = SONG_DB.get(song_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Song not found")
    return FileResponse(path, media_type="audio/mpeg")

@app.on_event("startup")
def on_startup():
    scan_and_upload()
    init_picture_db()

def start():
    scan_and_upload()
    init_picture_db()
    """Launched with `poetry run start` at root level"""
    uvicorn.run("vibeify_backend.main:app", host="0.0.0.0", port=8000, reload=True)

