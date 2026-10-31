
from datetime import datetime

import pytest

from edge.twin import DigitalTwin
from edge.models.healthState import HealthState


def createHealthState(heartRate, hrv, spo2, signalQuality):
    return HealthState(
        heartRate=heartRate,
        hrv=hrv,
        spo2=spo2,
        signalQuality=signalQuality,
        timestamp=datetime.now()
    )


def test_initial_state():
    twin = DigitalTwin()

    assert twin.getCurrentState() is None
    assert twin.getPersonalBaseline() is None


def test_accepts_valid_reading():
    twin = DigitalTwin()
    reading = createHealthState(72, 55, 98, 0.9)

    twin.update(reading)

    assert twin.getCurrentState() == reading


def test_ignores_low_quality_reading():
    twin = DigitalTwin()
    reading = createHealthState(72, 55, 98, 0.5)

    twin.update(reading)

    assert twin.getCurrentState() is None
    assert twin.getPersonalBaseline() is None


def test_calculates_personal_baseline():
    twin = DigitalTwin()

    reading1 = createHealthState(70, 50, 98, 0.9)
    reading2 = createHealthState(80, 60, 96, 0.9)

    twin.update(reading1)
    twin.update(reading2)

    baseline = twin.getPersonalBaseline()

    assert baseline["heartRate"] == pytest.approx(75)
    assert baseline["hrv"] == pytest.approx(55)
    assert baseline["spo2"] == pytest.approx(97)
