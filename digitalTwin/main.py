from datetime import datetime
from models.healthState import HealthState
from twin import DigitalTwin


twin = DigitalTwin()

readings = [
    HealthState(heartRate=70, hrv=45, spo2=98,
                signalQuality=0.95, timestamp=datetime.now()),

    HealthState(heartRate=72, hrv=43, spo2=98,
                signalQuality=0.92, timestamp=datetime.now()),

    HealthState(heartRate=75, hrv=40, spo2=97,
                signalQuality=0.90, timestamp=datetime.now())
]

for reading in readings:
    twin.update(reading)

print("\nTwinGuard Digital Twin")
print("----------------------")

print("Current state:")
print(twin.getCurrentState())

print("\nPersonal baseline:")
print(twin.getPersonalBaseline())