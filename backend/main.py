import os
from datetime import datetime
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client

load_dotenv()

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class Event(BaseModel):
    event_id: UUID
    patient_id: str
    state: str
    risk: float = Field(ge=0, le=1)
    hr: int = Field(gt=0, lt=300)
    timestamp: datetime


@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/patients/{patient_id}")
def get_patient(patient_id: str):
    result = supabase.table("patients").select("*").eq("id", patient_id).execute()
    return result.data


@app.post("/events")
def create_event(event: Event):
    row = event.model_dump(mode="json")
    try:
        result = supabase.table("events").upsert(row, on_conflict="event_id").execute()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "saved", "event": result.data[0]}

@app.get("/patients/{patient_id}/events")
def get_events(patient_id: str, limit: int = 50):
    result = (
        supabase.table("events")
        .select("*")
        .eq("patient_id", patient_id)
        .order("timestamp", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


@app.get("/patients/{patient_id}/twin")
def get_twin(patient_id: str):
    result = (
        supabase.table("events")
        .select("*")
        .eq("patient_id", patient_id)
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="No events yet for this patient")
    return result.data[0]
