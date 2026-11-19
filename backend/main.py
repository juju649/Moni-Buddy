import os
from datetime import datetime
from uuid import UUID

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

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
    personalBaseline: dict | None = None
    trendAnalysis: dict | None = None


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class AuthLogout(BaseModel):
    access_token: str = Field(min_length=1)


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/auth/login")
def login(credentials: AdminLogin):
    expected_username = os.getenv("MONIBUDDY_ADMIN_USERNAME", "admin")
    if credentials.username.strip().casefold() != expected_username.casefold():
        raise HTTPException(status_code=401, detail="Invalid username or password")

    admin_email = os.getenv("SUPABASE_ADMIN_EMAIL")
    anon_key = os.getenv("SUPABASE_ANON_KEY")
    if not admin_email or not anon_key:
        raise HTTPException(
            status_code=503,
            detail="Admin authentication is not configured. Set SUPABASE_ADMIN_EMAIL and SUPABASE_ANON_KEY.",
        )

    try:
        response = httpx.post(
            os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": admin_email, "password": credentials.password},
            timeout=10,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=503, detail="Supabase Auth timed out.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Could not reach Supabase Auth.") from exc

    if response.status_code in (400, 401):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if response.status_code == 429 or response.status_code >= 500:
        raise HTTPException(status_code=503, detail="Supabase Auth is temporarily unavailable.")
    if not response.is_success:
        raise HTTPException(status_code=502, detail="Supabase Auth rejected the login request.")

    try:
        session = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Supabase Auth returned an invalid response.") from exc
    if not isinstance(session, dict) or not session.get("access_token") or not session.get("refresh_token"):
        raise HTTPException(status_code=502, detail="Supabase Auth did not return a login session.")
    return {
        "username": expected_username,
        "access_token": session["access_token"],
        "refresh_token": session["refresh_token"],
    }


@app.post("/auth/logout")
def logout(request: AuthLogout):
    anon_key = os.getenv("SUPABASE_ANON_KEY")
    if not anon_key:
        raise HTTPException(
            status_code=503,
            detail="Admin authentication is not configured. Set SUPABASE_ANON_KEY.",
        )
    try:
        response = httpx.post(
            os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/logout",
            headers={
                "apikey": anon_key,
                "Authorization": "Bearer " + request.access_token,
            },
            timeout=10,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=503, detail="Supabase Auth timed out.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Could not reach Supabase Auth.") from exc

    if response.status_code not in (200, 204, 401, 403):
        raise HTTPException(status_code=503, detail="Supabase sign-out could not be confirmed.")
    return {"status": "signed out"}


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