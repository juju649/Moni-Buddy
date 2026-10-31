# Moni-Buddy
Building an accessible wearable device that monitors continuous ECG and PPG data using embedded AI to detect health trends, alert users and provide patients with non-invasive lifestyle suggestions and securely shares long-term data with physicians via telehealth to streamline care

Project Structure

digitalTw
in/
├── edge/          # Signal processing and health trend analysis
├── backend/       # FastAPI backend and Supabase connection
├── dashboard/     # Web dashboard
├── .env           # Local environment variables (not committed)
└── README.md

Requirements

Python 3.11 or later

Git

Supabase project and credentials

Setup

Clone the repository:

git clone https://github.com/juju649/Moni-Buddy.git
cd Moni-Buddy/digitalTwin

Create and activate a virtual environment:

python -m venv venv
.\venv\Scripts\Activate.ps1

Install edge dependencies:

pip install -r edge/requirements.txt

Install backend dependencies:

pip install -r backend/requirements.txt

Create a .env file in the digitalTwin root directory:

SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key

Replace the placeholders with your Supabase project credentials. Do not commit this file.

Running the Project

Run the edge processing module:

python -m edge.main

Start the FastAPI backend in a separate terminal:

python -m uvicorn backend.main:app --reload

The backend runs at http://127.0.0.1:8000.

The API documentation is available at http://127.0.0.1:8000/docs.
