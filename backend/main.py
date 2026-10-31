import os
from dotenv import load_dotenv
from fastapi import FastAPI
from supabase import create_client

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

app = FastAPI()

@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/patients/{patient_id}")
def get_patient(patient_id: str):
    result = supabase.table("patients").select("*").eq("id", patient_id).execute()
    return result.data