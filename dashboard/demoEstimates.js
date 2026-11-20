// ESTIMATED demo values for the Live Monitor (CardioTwin recording replay).
// Edit the numbers here. None of these are measured: the cards that use them carry an "Estimated" badge.
// They stay in the browser and are never sent to FastAPI or Supabase.
window.MONIBUDDY_ESTIMATES = {
  patients: {
    hamantha: {
      spo2: 97.2,                       // %
      recovery: { percent: 82, label: "Well recovered" },
      sleep: {
        bedtime: "11:05 PM",
        wakeTime: "7:15 AM",
        timeInBedMinutes: 490,
        sleepMinutes: 460,              // 7 h 40 min
        broken: false,
        // minutes per stage, in display order
        stages: [{ name: "Deep", minutes: 95 }, { name: "Light", minutes: 245 }, { name: "REM", minutes: 105 }, { name: "Awake", minutes: 15 }]
      }
    },
    sam: {
      spo2: 93.1,
      recovery: { percent: 28, label: "Poor recovery" },
      sleep: {
        bedtime: "1:20 AM",
        wakeTime: "7:05 AM",
        timeInBedMinutes: 345,
        sleepMinutes: 290,              // 4 h 50 min, broken
        broken: true,
        stages: [{ name: "Deep", minutes: 25 }, { name: "Light", minutes: 190 }, { name: "REM", minutes: 40 }, { name: "Awake", minutes: 90 }]
      }
    }
  },

  // Overall Health score (0-100) from the live heart rate, HRV and state plus the estimated sleep and recovery.
  // With these weights Hamantha lands near 86 and Sam near 41 during an alert.
  weights: { heartRate: 0.30, hrv: 0.10, sleep: 0.25, recovery: 0.25, state: 0.10 },
  stateScore: { normal: 100, irregular_rhythm: 55, elevated_hr: 50, low_hr: 50 },
  sleepTargetMinutes: 480,
  brokenSleepPenalty: 8,                // points off the sleep part for broken sleep
  hrvFullScoreMs: 40,                   // RMSSD at or above this gives a full HRV part

  // Small smooth drift so the numbers look alive without random jumps.
  drift: { spo2: 0.3, recovery: 1.0, periodSeconds: 90 },

  // Risk Trend: during an alert the risk rises by this step per logged event, up to the cap.
  riskRamp: { start: 0.72, step: 0.03, cap: 0.92 },

  footer: "Cards marked Estimated are illustrative and not measured. Moni-Buddy is a prototype and not a medical device."
};
