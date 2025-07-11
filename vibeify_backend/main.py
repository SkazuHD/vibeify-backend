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
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
FORCE = os.getenv("FORCE", "false").lower() in ("true", "1", "yes")

FALLBACK_IMAGE_PATH = "assets/albumart.jpg"
PLAYLIST_FALLBACK = "assets/playlist_default.png"
LIKED_PLAYLIST_FALLBACK = "assets/liked_playlist.png"
PROFILE_PICTURES_DIR = "profile_pictures"
PLAYLIST_PICTURE_DIR = "covers"

LIKED_PLAYLIST_ID = "liked_songs_virtual_playlist"  # Special ID for liked songs playlist

SONG_DB = {}  # song_id -> file_path
PFP_DB = {}  # user_id -> file_path
COVER_DB = {}  # song_id -> cover_path


def print_d(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)

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
        "year": str(get("TDRC")) if get("TDRC") else None,
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
    

@app.post("/upload/cover/{playlist_id}")
async def upload_cover(playlist_id: str, file: UploadFile = File(...)):
    """Upload a cover image for a playlist"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    os.makedirs(PLAYLIST_PICTURE_DIR, exist_ok=True)
    extension = ".jpg"  
    if "png" in file.content_type:
        extension = ".png"
    file_path = os.path.join(PLAYLIST_PICTURE_DIR, f"{playlist_id}{extension}")
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer) 
        COVER_DB[playlist_id] = file_path
        return {
            "message": "Cover uploaded successfully",
            "playlist_id": playlist_id,
            "file_path": file_path,
            "image_url": f"{BASE_URL}/cover/{quote(playlist_id)}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload cover: {str(e)}")
    
def init_cover_db():
    """Initialize the cover database from existing files"""
    global COVER_DB
    COVER_DB = {}
    if not os.path.exists(PLAYLIST_PICTURE_DIR):
        return
    for file in Path(PLAYLIST_PICTURE_DIR).glob("*.*"):
        playlist_id = file.stem
        COVER_DB[playlist_id] = str(file)
        print_d(f"Loaded cover for {playlist_id}: {file}")


def init_picture_db():
    """Initialize the profile picture database from existing files"""
    global PFP_DB
    PFP_DB = {}
    if not os.path.exists(PROFILE_PICTURES_DIR):
        return
    for file in Path(PROFILE_PICTURES_DIR).glob("*.*"):
        user_id = file.stem
        PFP_DB[user_id] = str(file)
        print_d(f"Loaded profile picture for {user_id}: {file}")



def scan_and_upload(base_dir="media"):
    print("📡 Scanning media directory...")
    base = Path(base_dir)
    songs_ref = db.collection(COLLECTION)
    
    # Load existing songs from Firestore first (if not forcing)
    existing_songs = set()
    if not FORCE:
        print_d("📥 Loading existing songs from Firestore...")
        try:
            docs = songs_ref.stream()
            for doc in docs:
                existing_songs.add(doc.id)
            print_d(f"📥 Found {len(existing_songs)} existing songs in Firestore")
        except Exception as e:
            print_d(f"❌ Error loading from Firestore: {e}")

    # Single file system scan
    uploaded_count = 0
    skipped_count = 0
    error_count = 0
    
    for file in base.rglob("*.mp3"):
        file_path = str(file)
        try:
            song_id = generate_stable_id(file_path)
            
            SONG_DB[song_id] = file_path

            # Check if already in Firestore (skip if FORCE is disabled)
            if not FORCE and song_id in existing_songs:
                print_d(f"⏩ Skipping already uploaded: {file_path} ({song_id})")
                skipped_count += 1
                continue

            metadata = extract_metadata(file_path)
            songs_ref.document(song_id).set(metadata)
            
            if FORCE and song_id in existing_songs:
                print_d(f"🔄 Force updated: {metadata['name']}")
            else:
                print_d(f"📤 Uploaded new song: {metadata['name']}")
            
            uploaded_count += 1

        except Exception as e:
            error_count += 1
            print_d(f"❌ Error processing {file_path}: {e}")

    print(f"📡 Media scan complete! Uploaded: {uploaded_count}, Skipped: {skipped_count}, Errors: {error_count}")


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
        print_d(f"Could not get image for {song_id}: {e}")
        return _get_fallback_image()
    
    
@app.get("/picture/{user_id}")
def get_profile_picture(user_id: str):
    path = PFP_DB.get(user_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Profile picture not found")

    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg")

@app.get("/cover/playlist/{playlist_id}")
def get_playlist_cover(playlist_id: str):
    path = COVER_DB.get(playlist_id)
    if playlist_id.lower() == LIKED_PLAYLIST_ID.lower():
        return _get_liked_playlist_cover()
    if not path or not os.path.isfile(path):
        return _get_playlist_fallback_image()
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg")

def _get_fallback_image():
    if not os.path.isfile(FALLBACK_IMAGE_PATH):
        raise HTTPException(status_code=500, detail="Fallback image not found")
    with open(FALLBACK_IMAGE_PATH, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg")

def _get_playlist_fallback_image():
    if not os.path.isfile(PLAYLIST_FALLBACK):
        raise HTTPException(status_code=500, detail="Fallback image not found")
    with open(PLAYLIST_FALLBACK, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg")

def _get_liked_playlist_cover():
    if not os.path.isfile(LIKED_PLAYLIST_FALLBACK):
        raise HTTPException(status_code=500, detail="Fallback image not found")
    with open(LIKED_PLAYLIST_FALLBACK, "rb") as f:
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
    init_cover_db()

def start():
    scan_and_upload()
    init_picture_db()
    init_cover_db()
    """Launched with `poetry run start` at root level"""
    uvicorn.run("vibeify_backend.main:app", host="0.0.0.0", port=8000, reload=True)


