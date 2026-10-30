from dataclasses import dataclass
from datetime import datetime

@dataclass
class HealthState:
    heartRate: float
    hrv: float
    spo2: float
    signalQuality: float
    timestamp: datetime