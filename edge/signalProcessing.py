
import numpy as np
import pandas as pd
from scipy.signal import butter, sosfiltfilt, find_peaks


# Load ECG and PPG data from CSV
def loadData(filePath, samplingRate=250):
    sampleData = pd.read_csv(filePath)

    requiredColumns = ["timestamp", "ecg", "ppg"]

    for column in requiredColumns:
        if column not in sampleData.columns:
            raise ValueError(f"Missing required column: {column}")

    if sampleData.empty:
        raise ValueError("The sample data file is empty.")

    return sampleData


# Remove unwanted frequencies from a signal
def cleanSignal(signal, samplingRate, lowCutoff, highCutoff):
    signal = np.asarray(signal, dtype=float)

    if not np.all(np.isfinite(signal)):
        raise ValueError("Signal contains missing or invalid values.")

    nyquist = samplingRate / 2

    if not (0 < lowCutoff < highCutoff < nyquist):
        raise ValueError("Invalid filter cutoff frequencies.")

    # Create Butterworth bandpass filter
    filterCoefficients = butter(
        4,
        [lowCutoff, highCutoff],
        btype="bandpass",
        fs=samplingRate,
        output="sos"
    )

    # Apply filter
    cleanedSignal = sosfiltfilt(filterCoefficients, signal)

    return cleanedSignal


# Detect ECG peaks and calculate heart rate
def calculateHeartRate(ecgSignal, samplingRate):
    ecgSignal = np.asarray(ecgSignal, dtype=float)

    if len(ecgSignal) < samplingRate * 2:
        raise ValueError("At least 2 seconds of ECG data are required.")

    # Set minimum distance between detected heartbeats
    minimumDistance = int(samplingRate * 0.4)

    # Set peak detection prominence
    peakProminence = max(np.std(ecgSignal) * 0.5, 0.05)

    # Detect ECG peaks
    peaks, peakProperties = find_peaks(
        ecgSignal,
        distance=minimumDistance,
        prominence=peakProminence
    )

    if len(peaks) < 2:
        return {
            "heartRate": None,
            "peaks": peaks
        }

    # Calculate time between heartbeats
    rrIntervals = np.diff(peaks) / samplingRate

    # Calculate average heart rate
    heartRate = 60 / np.mean(rrIntervals)

    return {
        "heartRate": round(heartRate, 2),
        "peaks": peaks
    }


# Calculate HRV using RMSSD
def calculateHRV(peaks, samplingRate):
    if len(peaks) < 3:
        return None

    # Calculate intervals between ECG peaks in milliseconds
    rrIntervals = np.diff(peaks) / samplingRate * 1000

    # Calculate differences between consecutive intervals
    intervalDifferences = np.diff(rrIntervals)

    # Calculate RMSSD
    rmssd = np.sqrt(np.mean(intervalDifferences ** 2))

    return round(rmssd, 2)

# Run the complete signal processing pipeline
def processSignals(filePath, samplingRate=250):
    sampleData = loadData(filePath, samplingRate)

    cleanedECG = cleanSignal(
        sampleData["ecg"], samplingRate, 0.5, 40
    )

    cleanedPPG = cleanSignal(
        sampleData["ppg"], samplingRate, 0.5, 8
    )

    # Check the quality of both signals
    ecgQuality = calculateSignalQuality(cleanedECG)
    ppgQuality = calculateSignalQuality(cleanedPPG)

    # Use the lower score as the overall signal quality
    signalQuality = min(ecgQuality, ppgQuality)

    # Stop if either signal fails the quality checks
    if signalQuality < 0.7:
        return {
            "signalQuality": signalQuality,
            "status": "poor_signal",
            "heartRate": None,
            "hrv": None,
            "ecgPeaks": 0
        }

    heartRateResults = calculateHeartRate(cleanedECG, samplingRate)
    hrv = calculateHRV(heartRateResults["peaks"], samplingRate)

    return {
        "signalQuality": signalQuality,
        "status": "success",
        "heartRate": heartRateResults["heartRate"],
        "hrv": hrv,
        "ecgPeaks": len(heartRateResults["peaks"]),
        "cleanedECG": cleanedECG,
        "cleanedPPG": cleanedPPG
    }

#Signal Quality Check ------------

def calculateSignalQuality(signal):
    signal = np.asarray(signal, dtype=float)

    if len(signal) == 0:
        return 0.0

    # Reject missing or invalid values
    if not np.all(np.isfinite(signal)):
        return 0.0

    # Reject signals that are almost completely flat
    if np.std(signal) < 0.01:
        return 0.0

    # Check for sudden, unusually large changes
    signalChanges = np.abs(np.diff(signal))

    if len(signalChanges) > 0:
        largeChanges = np.sum(
            signalChanges > (np.std(signal) * 5)
        )
        noisyPercent = largeChanges / len(signalChanges)

        if noisyPercent > 0.05:
            return 0.0

    return 1.0