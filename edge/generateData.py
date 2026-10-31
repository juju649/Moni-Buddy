#Temp mock data for testing purposes
import neurokit2 as nk
import pandas as pd
import numpy as np
import os

# Signal settings
samplingRate = 250
duration = 60

# Generate synthetic ECG signal
ecg = nk.ecg_simulate(
    duration=duration,
    sampling_rate=samplingRate,
    heart_rate=72,
    heart_rate_std=3,
    method="ecgsyn"
)

# Generate synthetic PPG signal
ppg = nk.ppg_simulate(
    duration=duration,
    sampling_rate=samplingRate,
    heart_rate=72
)

# Generate timestamps
timestamps = np.arange(len(ecg)) / samplingRate

# Combine signals into a DataFrame
sampleData = pd.DataFrame({
    "timestamp": timestamps,
    "ecg": ecg,
    "ppg": ppg
})

# Make sure the data folder exists
os.makedirs("data", exist_ok=True)

# Save the generated data
sampleData.to_csv("data/sampleData.csv", index=False)

print("Synthetic ECG and PPG data generated:")
print("Sampling rate:", samplingRate, "Hz")
print("Duration:", duration, "seconds")
print("Total samples:", len(sampleData))
print("\nFirst 5 readings:")
print(sampleData.head())
