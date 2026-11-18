from datetime import datetime
from uuid import uuid4

<<<<<<< Updated upstream
=======
import httpx
import numpy as np

>>>>>>> Stashed changes
from edge.models.healthState import HealthState
from edge.twin import DigitalTwin
from edge.signalProcessing import processSignals
from edge.trendAnalysis import TrendAnalyzer

<<<<<<< Updated upstream
import httpx
=======
API_URL = "http://127.0.0.1:8000/events"


def to_json_compatible(value):
    if isinstance(value, dict):
        return {key: to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_compatible(item) for item in value]
    if isinstance(value, np.ndarray):
        return to_json_compatible(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


def classify(hr):
    """Turn a heart rate into one of the states the dashboard understands."""
    if hr > 100:
        return "elevated_hr", min(0.5 + (hr - 100) / 100, 1.0)
    if hr < 50:
        return "low_hr", min(0.5 + (50 - hr) / 50, 1.0)
    return "normal", 0.1

>>>>>>> Stashed changes

# Create the digital twin
twin = DigitalTwin()

# Process ECG and PPG data
results = processSignals("edge/data/sampleData.csv")

print("Moni-Buddy")
print("----------------------")

# Check signal quality before updating the twin
if results["status"] == "success":

    # Create a health state using processed measurements
    healthState = HealthState(
        heartRate=results["heartRate"],
        hrv=results["hrv"],
        spo2=None,
        signalQuality=results["signalQuality"],
        timestamp=datetime.now()
    )

    # Update the digital twin
    twin.update(healthState)
    personalBaseline = twin.getPersonalBaseline()

    print("\nProcessed Measurements:")
    print("Heart Rate:", healthState.heartRate, "BPM")
    print("HRV:", healthState.hrv, "ms")
    print("SpO2: Not available")
    print("Signal Quality:", healthState.signalQuality)

    print("\nPersonal Baseline:")
    print(personalBaseline)

    # Analyze trends against the personal baseline
    analyzer = TrendAnalyzer()
<<<<<<< Updated upstream

    trendResults = analyzer.analyze(
        healthState,
        twin.getPersonalBaseline()
    )
=======
    trendResults = analyzer.analyze(healthState, personalBaseline)
>>>>>>> Stashed changes

    print("\nTrend Analysis:")
    print(trendResults)

<<<<<<< Updated upstream
=======
    # DEMO ONLY: run "python -m edge.main 135" to simulate a high heart rate
    hr = round(healthState.heartRate)
    if len(sys.argv) > 1:
        hr = int(sys.argv[1])
        print(f"\n[Demo mode] Using simulated heart rate: {hr} BPM")

    state, risk = classify(hr)

    eventData = {
        "event_id": str(uuid4()),
        "patient_id": "p1",
        "state": state,
        "risk": round(risk, 2),
        "hr": hr,
        "timestamp": datetime.now().astimezone().isoformat(),
        "personalBaseline": to_json_compatible(personalBaseline),
        "trendAnalysis": to_json_compatible(trendResults),
    }

    response = httpx.post(API_URL, json=eventData, timeout=10)

    print("\nBackend Response:")
    print(response.status_code)
    print(response.json())
>>>>>>> Stashed changes
else:
    print("Poor signal quality. Digital twin was not updated.")

    # Prepare event data for the backend
eventData = {
    "event_id": str(uuid4()),
    "patient_id": "p1",
    "state": "normal",
    "risk": 0.0,
    "hr": round(healthState.heartRate),
    "timestamp": datetime.now().astimezone().isoformat()
}

# Send event to FastAPI
response = httpx.post(
    "http://127.0.0.1:8000/events",
    json=eventData,
    timeout=10
)

print("\nBackend Response:")
print(response.status_code)
print(response.json())