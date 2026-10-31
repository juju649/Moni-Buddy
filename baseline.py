class PersonalBaseline:
    def __init__(self):
        self.heartRates = []
        self.hrvValues = []
        self.spo2Values = []

    def addReading(self, healthState):
        self.heartRates.append(healthState.heartRate)

        if healthState.hrv is not None:
            self.hrvValues.append(healthState.hrv)

        if healthState.spo2 is not None:
            self.spo2Values.append(healthState.spo2)

    def getBaseline(self):
        if not self.heartRates:
            return None

        return {
            "heartRate": sum(self.heartRates) / len(self.heartRates),
            "hrv": (
                sum(self.hrvValues) / len(self.hrvValues)
                if self.hrvValues else None
            ),
            "spo2": (
                sum(self.spo2Values) / len(self.spo2Values)
                if self.spo2Values else None
            )
        }