// CardioTwin watch over USB serial (OPTIONAL). Based on watchScreen (ST7789 135x240, LED on 17, button on 18).
// The laptop runs:  python live_bridge.py --watch-port COMx   and sends one line per second:
//     W,<patient>,<bpm>,<state>        example:  W,sam,126,elevated_hr
// Alert (state elevated_hr or low_hr): red LED flashes, buzzer pulses, red alert card for 10 seconds.
// Press the button to dismiss the alert card early.
// Values come from a RECORDING replayed on the SK-AM62B or the laptop. Not live sensor data. Not a medical device.
// Close the Arduino Serial Monitor before starting the bridge: only one program can use the port.
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>

// Display pins (same as watchScreen)
#define TFT_CS        7
#define TFT_DC        39
#define TFT_RST       40
#define TFT_BACKLIGHT 45
#define SPI_SCK  36
#define SPI_MISO 37
#define SPI_MOSI 35

#define BUTTON_PIN 18   // external button (other leg to GND)
#define LED_PIN    17   // external red LED (through a resistor to GND)
const int BUZZ_PIN = -1;  // set to your buzzer pin, for example 16. -1 = no buzzer

const unsigned long ALERT_CARD_MS = 10000;
const unsigned long STALE_MS = 5000;

Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);

String lineBuf;
String patient = "--";
String state = "waiting";
int bpm = 0;
bool alertOn = false;          // current reading is an alert
unsigned long alertCardUntil = 0;
unsigned long lastRx = 0;
bool lastButton = HIGH;
bool dirty = true;

bool isAlertState(const String& s) { return s == "elevated_hr" || s == "low_hr"; }

String niceName(const String& p) {
  if (p == "hamantha") return "Hamantha";
  if (p == "sam") return "Sam";
  return p;
}

String niceState(const String& s) {
  if (s == "elevated_hr") return "HIGH HR";
  if (s == "low_hr") return "LOW HR";
  if (s == "irregular_rhythm") return "IRREGULAR";
  if (s == "normal") return "NORMAL";
  return "WAITING";
}

void drawNormal() {
  uint16_t accent = alertOn ? ST77XX_RED : ST77XX_GREEN;
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(2);
  tft.setCursor(8, 10);
  tft.print(niceName(patient));
  tft.setTextSize(5);
  tft.setTextColor(accent);
  tft.setCursor(8, 70);
  if (bpm > 0) tft.print(bpm); else tft.print("--");
  tft.setTextSize(2);
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(8, 120);
  tft.print("BPM");
  tft.setTextColor(accent);
  tft.setCursor(8, 160);
  tft.print(niceState(state));
  tft.setTextSize(1);
  tft.setTextColor(0x9CD3);
  tft.setCursor(8, 215);
  tft.print(millis() - lastRx > STALE_MS ? "no data from laptop" : "recording replay");
}

void drawAlertCard() {
  tft.fillScreen(ST77XX_RED);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(3);
  tft.setCursor(8, 20);
  tft.print("ALERT");
  tft.setTextSize(2);
  tft.setCursor(8, 65);
  tft.print(niceName(patient));
  tft.setTextSize(5);
  tft.setCursor(8, 100);
  tft.print(bpm);
  tft.setTextSize(2);
  tft.setCursor(8, 150);
  tft.print(niceState(state));
  tft.setTextSize(1);
  tft.setCursor(8, 200);
  tft.print("press button to dismiss");
}

void handleLine(String s) {
  s.trim();
  if (!s.startsWith("W,")) return;
  int a = s.indexOf(','), b = s.indexOf(',', a + 1), c = s.indexOf(',', b + 1);
  if (b < 0 || c < 0) return;
  String newPatient = s.substring(a + 1, b);
  int newBpm = s.substring(b + 1, c).toInt();
  String newState = s.substring(c + 1);
  bool newAlert = isAlertState(newState);
  if (newAlert && (!alertOn || newPatient != patient)) alertCardUntil = millis() + ALERT_CARD_MS;  // a new alert starts
  if (!newAlert) alertCardUntil = 0;
  patient = newPatient;
  bpm = newBpm;
  state = newState;
  alertOn = newAlert;
  lastRx = millis();
  dirty = true;
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  if (BUZZ_PIN >= 0) pinMode(BUZZ_PIN, OUTPUT);
  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI, TFT_CS);
  pinMode(TFT_BACKLIGHT, OUTPUT);
  digitalWrite(TFT_BACKLIGHT, HIGH);
  tft.init(135, 240);
  tft.setRotation(0);  // portrait. Try 2 if upside down.
  drawNormal();
}

void loop() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') { handleLine(lineBuf); lineBuf = ""; }
    else if (lineBuf.length() < 80) lineBuf += ch;
  }

  bool button = digitalRead(BUTTON_PIN);
  if (lastButton == HIGH && button == LOW && alertCardUntil) { alertCardUntil = 0; dirty = true; delay(30); }
  lastButton = button;

  unsigned long now = millis();
  bool cardActive = alertCardUntil && now < alertCardUntil;
  if (alertCardUntil && now >= alertCardUntil) { alertCardUntil = 0; dirty = true; }

  // Red LED flashes and the buzzer pulses while the alert card is up (250 ms on, 250 ms off).
  bool pulse = cardActive && ((now / 250) % 2 == 0);
  digitalWrite(LED_PIN, pulse ? HIGH : LOW);
  if (BUZZ_PIN >= 0) digitalWrite(BUZZ_PIN, pulse ? HIGH : LOW);

  static unsigned long lastDraw = 0;
  if (dirty || now - lastDraw > 1000) {
    if (cardActive) drawAlertCard(); else drawNormal();
    dirty = false;
    lastDraw = now;
  }
}
