class PersonalBaseline:

    def __init__(self):
        self.heartRates = []
        self.hrvValues = []
        self.spo2Values = []

    def addReading(self, healthState):
        self.heartRates.append(healthState.heartRate)
        self.hrvValues.append(healthState.hrv)
        self.spo2Values.append(healthState.spo2)

    def getBaseline(self):
        if not self.heartRates:
            return None

        return {
            "heartRate": sum(self.heartRates) / len(self.heartRates),
            "hrv": sum(self.hrvValues) / len(self.hrvValues),
            "spo2": sum(self.spo2Values) / len(self.spo2Values)
        }