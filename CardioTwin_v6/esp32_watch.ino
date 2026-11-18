// CardioTwin watch display for an ESP32. UNTESTED on real hardware.
// Reads the newest result from the laptop over Wi-Fi once a second.
// Laptop side: in the dashboard, SK-AM62B board tab, press "Start watch server (Wi-Fi)".
// Reply format: hr,state,risk,quality_score,valid   example: 94,normal,0.10,98,1
// Not a medical device. The numbers come from a recording processed on the board.
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID = "YOUR_WIFI_OR_HOTSPOT";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* URL       = "http://LAPTOP_IP:8080/latest.txt";  // use the address the dashboard shows
const int   BUZZ_PIN  = -1;  // set to a pin number if your watch has a vibration motor

void show(int hr, const String& state, float risk, int sqi, bool valid) {
  // Replace with your display code (TFT, OLED, e-paper). Serial is the fallback.
  if (!valid) { Serial.println("HR --  waiting for a good signal"); return; }
  Serial.printf("HR %d  state %s  risk %.2f  quality %d\n", hr, state.c_str(), risk, sqi);
}

void setup() {
  Serial.begin(115200);
  if (BUZZ_PIN >= 0) pinMode(BUZZ_PIN, OUTPUT);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.println("\nWiFi ok");
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.setTimeout(2000);
    http.begin(URL);
    if (http.GET() == 200) {
      String s = http.getString();
      s.trim();
      int a = s.indexOf(','), b = s.indexOf(',', a + 1), c = s.indexOf(',', b + 1), d = s.indexOf(',', c + 1);
      if (d > 0) {
        int hr = s.substring(0, a).toInt();
        String state = s.substring(a + 1, b);
        float risk = s.substring(b + 1, c).toFloat();
        int sqi = s.substring(c + 1, d).toInt();
        bool valid = s.substring(d + 1).toInt() == 1;
        show(hr, state, risk, sqi, valid);
        if (valid && state != "normal" && BUZZ_PIN >= 0) { digitalWrite(BUZZ_PIN, HIGH); delay(200); digitalWrite(BUZZ_PIN, LOW); }
      }
    } else {
      Serial.println("Cannot reach the laptop");
    }
    http.end();
  } else {
    WiFi.reconnect();
  }
  delay(1000);
}
