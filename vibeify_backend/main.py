import uvicorn
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello deine Mum"}

def start():
    """Launched with `poetry run start` at root level"""
    uvicorn.run("moody_backend.main:app", host="0.0.0.0", port=8000, reload=True)