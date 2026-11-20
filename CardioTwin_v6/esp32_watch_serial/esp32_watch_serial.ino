// MoniBuddy watch demo for the Adafruit Feather ESP32-S3 Reverse TFT (135 x 240)
// Two patients. Nothing changes automatically. Press the button to switch patients.
//   Patient 1: Zia's real AFE4950 recording, normal heart rate. The recording repeats until you press the button.
//   Patient 2 (simulated): starts at 110 BPM, climbs to 120 BPM over 3 seconds, then the alert starts.
//      Alert card, red LED flashing and buzzer for 10 seconds.
//      Then the heart rate falls slowly (about 1.5 BPM per second) to 85 BPM and keeps running there.
// Each button press jumps to the other patient. Patient 2 always starts from 110 BPM.
// Patient 2 is a scripted simulation for the demo. Not a diagnosis.

#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <math.h>

// Display pins
#define TFT_CS        7
#define TFT_DC        39
#define TFT_RST       40
#define TFT_BACKLIGHT 45

// SPI pins
#define SPI_SCK  36
#define SPI_MISO 37
#define SPI_MOSI 35

#define BUTTON_PIN 18   // button, other leg to GND
#define LED_PIN    17   // red LED (through a resistor), other leg to GND
#define BUZZER_PIN 16   // buzzer, other leg to GND

// Buzzer type: 0 = active buzzer (beeps when the pin is HIGH)
//              1 = passive buzzer (needs a tone)
#define BUZZER_PASSIVE 0
#define BUZZER_FREQ    2000

// Scenario timing, change these to taste
const float    P2_START_BPM = 110.0f;   // patient 2 starts here
const float    P2_PEAK_BPM  = 120.0f;   // and climbs to this
const uint32_t RISE_MS    = 3000;    // time to climb before the alert starts
const uint32_t ALERT_MS   = 10000;   // alert length for patient 2
const uint32_t FLASH_MS   = 250;     // LED and buzzer on time, then off for the same time
const float    FALL_BPM_PER_S = 1.5f;   // how fast the heart rate comes down after the alert
const float    RESTING_BPM    = 85.0f;  // where patient 2 settles

const float HIGH_BPM = 100.0f;       // same limits as the MoniBuddy classifier
const float LOW_BPM  = 50.0f;

#define W 135
#define H 240

// ---------------------------------------------------------------
// RECORDED HEART RATE, one value per second (55 seconds each).
// Both lists come from the same SK-AM62B board pipeline (smoothed heart rate).
// ---------------------------------------------------------------
// Normal heart rate: AFE4950 recording (Zia) (91.5 to 98.0 BPM, median 93.75)
const float bpmP1[] = {
  98.0, 97.6, 97.2, 95.9, 94.9, 94.9, 94.5, 93.8, 94.9, 93.8, 93.8, 92.6, 92.6, 92.6,
  92.6, 92.6, 92.6, 92.9, 93.3, 93.3, 93.8, 93.8, 94.3, 94.3, 94.3, 94.0, 94.0, 93.9,
  93.9, 94.0, 94.0, 94.0, 94.0, 94.2, 94.0, 94.0, 94.0, 92.0, 92.0, 92.0, 92.0, 92.0,
  92.0, 92.0, 92.0, 92.0, 91.7, 91.7, 91.5, 91.5, 91.5, 91.5, 91.5, 91.5, 91.5
};
const int BPM_COUNT = sizeof(bpmP1) / sizeof(bpmP1[0]);

const uint32_t TICK_MS   = 25;     // animation step
const int      TRACE_Y   = 205;    // baseline of the ECG trace
const int      TRACE_AMP = 28;     // height of the R peak in pixels

Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);
GFXcanvas16 canvas(W, H);          // draw off screen, then push once (no flicker)

enum Phase { PH_P1, PH_RISE, PH_ALERT, PH_RECOVER };
Phase phase_ = PH_P1;
uint32_t phaseStart = 0;
int patient = 1;

float phase = 0;                   // position inside the current beat, 0 to 1
float bpm = 94;
uint32_t lastTick = 0;
int8_t trace[W];

bool lastBtn = HIGH;
uint32_t lastEdge = 0;

// ---------------- buzzer and LED ----------------
void buzzer(bool on) {
#if BUZZER_PASSIVE
  if (on) tone(BUZZER_PIN, BUZZER_FREQ); else noTone(BUZZER_PIN);
  if (!on) digitalWrite(BUZZER_PIN, LOW);
#else
  digitalWrite(BUZZER_PIN, on ? HIGH : LOW);
#endif
}

void updateOutputs() {
  bool on = false;
  if (phase_ == PH_ALERT) on = (((millis() - phaseStart) / FLASH_MS) % 2) == 0;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
  buzzer(on);
}

// ---------------- scenario ----------------
void startPatient1() {
  phase_ = PH_P1;
  patient = 1;
  phaseStart = millis();
  bpm = bpmP1[0];
  memset(trace, 0, sizeof(trace));
}

void startPatient2() {
  phase_ = PH_RISE;
  patient = 2;
  phaseStart = millis();
  bpm = P2_START_BPM;
  memset(trace, 0, sizeof(trace));
}

void updatePhase() {
  uint32_t el = millis() - phaseStart;
  if (phase_ == PH_RISE && el >= RISE_MS) {
    phase_ = PH_ALERT;
    phaseStart = millis();
  } else if (phase_ == PH_ALERT && el >= ALERT_MS) {
    phase_ = PH_RECOVER;
    phaseStart = millis();
  }
  // Patient 1 and the end of patient 2 keep running until the button is pressed.
}

void handleButton() {
  bool b = digitalRead(BUTTON_PIN);
  uint32_t now = millis();
  if (b != lastBtn && now - lastEdge > 30) {   // 30 ms debounce on press and release
    lastEdge = now;
    lastBtn = b;
    if (b == LOW) {
      if (patient == 1) startPatient2(); else startPatient1();
    }
  }
}

bool isAbnormal() { return bpm > HIGH_BPM || bpm < LOW_BPM; }

// recorded value for the current second, blended to the next one
float recorded(const float* data) {
  uint32_t ms = millis() - phaseStart;
  int idx = (ms / 1000) % BPM_COUNT;
  float t = (ms % 1000) / 1000.0f;
  float a = data[idx];
  float b = data[(idx + 1) % BPM_COUNT];
  return a + (b - a) * t;
}

// ---------------- animation ----------------
// Gaussian bump used to build the ECG shape
float g(float x, float mu, float w) {
  float d = (x - mu) / w;
  return expf(-d * d);
}

// One ECG beat: P wave, Q dip, R spike, S dip, T wave
float ecg(float p) {
  return 0.15f * g(p, 0.15f, 0.030f)
       - 0.12f * g(p, 0.27f, 0.012f)
       + 1.00f * g(p, 0.30f, 0.012f)
       - 0.25f * g(p, 0.33f, 0.014f)
       + 0.30f * g(p, 0.55f, 0.050f);
}

void drawHeart(int cx, int cy, float r) {
  uint16_t c = ST77XX_RED;
  canvas.fillCircle(cx - 0.9f * r, cy, r, c);
  canvas.fillCircle(cx + 0.9f * r, cy, r, c);
  canvas.fillTriangle(cx - 1.85f * r, cy + 0.35f * r,
                      cx + 1.85f * r, cy + 0.35f * r,
                      cx, cy + 2.4f * r, c);
}

void centerText(const char *s, int y, int size, uint16_t color) {
  int16_t x1, y1;
  uint16_t tw, th;
  canvas.setTextSize(size);
  canvas.setTextColor(color);
  canvas.getTextBounds(s, 0, 0, &x1, &y1, &tw, &th);
  canvas.setCursor((W - tw) / 2, y);
  canvas.print(s);
}

// Alert card that sits on top of the animation
void drawAlert() {
  uint16_t border = ((millis() / 250) % 2) ? ST77XX_RED : ST77XX_WHITE;

  canvas.fillRoundRect(8, 30, 119, 118, 10, 0x4800);          // dark red card
  canvas.drawRoundRect(8, 30, 119, 118, 10, border);
  canvas.drawRoundRect(9, 31, 117, 116, 9, border);

  // warning triangle with an exclamation mark
  canvas.fillTriangle(67, 38, 51, 66, 83, 66, ST77XX_YELLOW);
  canvas.setTextSize(2);
  canvas.setTextColor(ST77XX_BLACK);
  canvas.setCursor(61, 47);
  canvas.print("!");

  centerText("ALERT", 74, 3, ST77XX_WHITE);
  centerText("High heart rate", 104, 1, ST77XX_YELLOW);

  char buf[16];
  snprintf(buf, sizeof(buf), "%d BPM", (int)(bpm + 0.5f));
  centerText(buf, 122, 2, ST77XX_WHITE);
}

void step() {
  updatePhase();

  if (phase_ == PH_P1) {
    bpm = recorded(bpmP1);                       // your recording
  } else if (phase_ == PH_RISE) {
    uint32_t el = millis() - phaseStart;
    bpm = P2_START_BPM + (P2_PEAK_BPM - P2_START_BPM) * (el / (float)RISE_MS);   // 110 to 120
  } else if (phase_ == PH_ALERT) {
    bpm = P2_PEAK_BPM + 0.6f * sinf(millis() / 700.0f);   // holds near 120 with tiny natural variation
  } else {
    bpm -= FALL_BPM_PER_S * TICK_MS / 1000.0f;   // slow, steady fall
    if (bpm < RESTING_BPM) bpm = RESTING_BPM + 0.5f * sinf(millis() / 900.0f);   // settles and keeps running
  }

  // advance the beat
  phase += TICK_MS * bpm / 60000.0f;
  if (phase >= 1.0f) phase -= 1.0f;

  // scroll the trace left by one pixel and add a new point
  memmove(trace, trace + 1, W - 1);
  trace[W - 1] = (int8_t)(-ecg(phase) * TRACE_AMP);
}

void drawFrame() {
  canvas.fillScreen(ST77XX_BLACK);

  centerText(patient == 1 ? "PATIENT 1" : "PATIENT 2", 6, 1, ST77XX_WHITE);
  centerText(patient == 1 ? "Zia's recording" : "Simulated", 18, 1, 0x7BEF);

  // heart grows on the R spike and again on the T wave
  float pulse = g(phase, 0.30f, 0.07f) + 0.5f * g(phase, 0.55f, 0.08f);
  drawHeart(W / 2, 56, 12 + 4 * pulse);

  // BPM number, yellow when outside the range
  uint16_t numColor = isAbnormal() ? ST77XX_YELLOW : ST77XX_WHITE;
  char buf[8];
  snprintf(buf, sizeof(buf), "%d", (int)(bpm + 0.5f));
  centerText(buf, 112, 4, numColor);
  centerText("BPM", 150, 2, ST77XX_RED);

  // state label
  if (isAbnormal()) centerText(bpm > HIGH_BPM ? "HIGH" : "LOW", 172, 1, ST77XX_YELLOW);
  else centerText("NORMAL", 172, 1, ST77XX_GREEN);

  // ECG trace
  canvas.drawFastHLine(0, TRACE_Y, W, 0x2104);
  for (int i = 1; i < W; i++) {
    canvas.drawLine(i - 1, TRACE_Y + trace[i - 1], i, TRACE_Y + trace[i], ST77XX_GREEN);
  }

  if (phase_ == PH_ALERT) drawAlert();

  tft.drawRGBBitmap(0, 0, canvas.getBuffer(), W, H);
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  pinMode(BUZZER_PIN, OUTPUT);
  buzzer(false);

  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, TFT_CS);

  pinMode(TFT_BACKLIGHT, OUTPUT);
  digitalWrite(TFT_BACKLIGHT, HIGH);

  tft.init(135, 240);
  tft.setRotation(0);
  tft.fillScreen(ST77XX_BLACK);

  startPatient1();
  lastTick = millis();
}

void loop() {
  handleButton();
  updateOutputs();

  uint32_t now = millis();
  if (now - lastTick >= TICK_MS) {
    lastTick = now;
    step();
    drawFrame();
  }
}