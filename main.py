import httpx
from uuid import uuid4
from datetime import datetime
from models.healthState import HealthState
from twin import DigitalTwin
from signalProcessing import processSignals
from trendAnalysis import TrendAnalyzer

# Create the digital twin
twin = DigitalTwin()

# Process ECG and PPG data
results = processSignals("data/sampleData.csv")

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

    print("\nProcessed Measurements:")
    print("Heart Rate:", healthState.heartRate, "BPM")
    print("HRV:", healthState.hrv, "ms")
    print("SpO2: Not available")
    print("Signal Quality:", healthState.signalQuality)

    print("\nPersonal Baseline:")
    print(twin.getPersonalBaseline())

    # Analyze trends against the personal baseline
    analyzer = TrendAnalyzer()

    trendResults = analyzer.analyze(
        healthState,
        twin.getPersonalBaseline()
    )

    print("\nTrend Analysis:")
    print(trendResults)

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