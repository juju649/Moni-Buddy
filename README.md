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
SUPABASE_ANON_KEY=your_supabase_publishable_or_anon_key
SUPABASE_ADMIN_EMAIL=the_admin_user_email_in_supabase_auth
MONIBUDDY_ADMIN_USERNAME=admin

Keep `SUPABASE_KEY` server-side; it is used by the existing database client and must never be added to dashboard HTML. Set `SUPABASE_ANON_KEY` to your Supabase project's publishable/anon key. The login endpoint uses this public key to call Supabase Auth; it does not use the database secret key.

In the Supabase Dashboard, open **Authentication → Users** and create/confirm the admin user using the email set in `SUPABASE_ADMIN_EMAIL`. The dashboard username `admin` maps to this email. Set that user's password to `password` for the requested local demo credentials. Use a strong password instead for any shared or deployed environment. Do not put the password in `.env` or frontend code.

These settings are required for admin login. Demo profile selection does not use Supabase Auth and keeps its fictional readings in browser memory. The existing `/events` ingestion and patient data routes retain their current behavior.

The admin Auth session is kept in page memory and revoked when you log out; refreshing the page requires signing in again. Serve the dashboard and API over HTTPS outside local development. This login does not change authorization on the existing data API routes.

Running the Project

Run the edge processing module:

python -m edge.main

Start the FastAPI backend in a separate terminal:

python -m uvicorn backend.main:app --reload

The backend runs at http://127.0.0.1:8000.

The API documentation is available at http://127.0.0.1:8000/docs.

Serve the existing dashboard in a separate terminal from the `digitalTwin` directory:

python -m http.server 8768 --bind 127.0.0.1 --directory dashboard

Open http://127.0.0.1:8768. Log in with username `admin` and the password configured on the Supabase Auth user. Turn **Demo Mode** on at the login screen to choose **Healthy Hamantha** or **Sickly Sam** without a password. Their readings are simulated locally and are not posted to FastAPI or saved in Supabase.
