class TrendAnalyzer:

    #5 percent change

    def __init__(self, tolerancePercent=5):
        self.tolerancePercent = tolerancePercent

    def analyze(self, currentState, baseline):

        if baseline is None:
            return {
                "status": "insufficient_data",
                "measurements": {}
            }

        measurements = {
            "heartRate": currentState.heartRate,
            "hrv": currentState.hrv,
            "spo2": currentState.spo2
        }

        results = {}

        for name, currentValue in measurements.items():

            baselineValue = baseline.get(name)

            if baselineValue is None or baselineValue == 0:
                continue

            difference = currentValue - baselineValue

            percentChange = (
                difference / baselineValue
            ) * 100

            if abs(percentChange) <= self.tolerancePercent:
                trend = "close_to_baseline"

            elif difference > 0:
                trend = "above_baseline"

            else:
                trend = "below_baseline"

            results[name] = {
                "current": round(currentValue, 2),
                "baseline": round(baselineValue, 2),
                "difference": round(difference, 2),
                "percentChange": round(percentChange, 2),
                "trend": trend
            }

        return {
            "status": "success",
            "measurements": results
        }