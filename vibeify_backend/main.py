from datetime import datetime

import uvicorn
from fastapi import FastAPI
import firebase_admin
from firebase_admin import credentials, firestore

app = FastAPI()
# Firebase Init
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()
COLLECTION = "songs"

SONG_DB = {}  # song_id -> file_path

@app.get("/", response_model=dict)
async def root():
    return {"message": "Vibeify API is healthy!",
            "date": datetime.isoformat(datetime.today())}

def start():
    """Launched with `poetry run start` at root level"""
    uvicorn.run("vibeify_backend.main:app", host="0.0.0.0", port=8000, reload=True)