from models.healthState import HealthState
from baseline import PersonalBaseline


class DigitalTwin:

    def __init__(self):
        self.baseline = PersonalBaseline()
        self.currentState = None

    def update(self, healthState):
        if healthState.signalQuality < 0.7: #0.7 temp prototype setting
            print("Signal quality too low. Reading ignored.")
            return

        self.currentState = healthState
        self.baseline.addReading(healthState)

    def getCurrentState(self):
        return self.currentState

    def getPersonalBaseline(self):
        return self.baseline.getBaseline()