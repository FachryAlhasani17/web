#include <PZEM004Tv30.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <time.h>
#include <FastLED.h>

// ========== KONFIGURASI LED STATUS ==========
#define NUM_LEDS 1
#define LED_PIN 48
CRGB leds[NUM_LEDS];

// ========== KONFIGURASI WiFi ==========
const char* ssid = "SKK - STUDENT";
const char* password = "";

// ========== KONFIGURASI WEB SERVER ==========
const int webServerPort = 6528;
WebServer server(webServerPort);

// ========== DEVICE ID ==========
const char* device_id = "ESP32_001";

// ========== KONFIGURASI PZEM ==========
#define PZEM_RX_PIN 4
#define PZEM_TX_PIN 5

// ========== KONFIGURASI RELAY (4 RELAY - ACTIVE LOW) ==========
#define RELAY_1_PIN 10
#define RELAY_2_PIN 11
#define RELAY_3_PIN 12
#define RELAY_4_PIN 13

const int relayPins[] = {RELAY_1_PIN, RELAY_2_PIN, RELAY_3_PIN, RELAY_4_PIN};
const int numRelays = 4;

// ========== INTERVALS ==========
#define VOLTAGE_STABILIZATION_DELAY 2000

HardwareSerial PZEMSerial(1);
PZEM004Tv30 pzem(PZEMSerial, PZEM_RX_PIN, PZEM_TX_PIN);

// Status koneksi
bool pzemConnected = false;
bool wifiConnected = false;
bool ledStatusDone = false;

// ========== FUNGSI LED ==========
void ledBlinkRed() {
  leds[0] = CRGB::Red;
  FastLED.show();
  delay(200);
  leds[0] = CRGB::Black;
  FastLED.show();
  delay(200);
}

void ledBlinkGreen3Times() {
  for(int i = 0; i < 3; i++) {
    leds[0] = CRGB::Green;
    FastLED.show();
    delay(200);
    leds[0] = CRGB::Black;
    FastLED.show();
    delay(200);
  }
  ledStatusDone = true;
}

// ========== FUNGSI RELAY ==========
void setupRelays() {
  Serial.println("[RELAY] Initializing 4 relays...");
  for(int i = 0; i < numRelays; i++) {
    pinMode(relayPins[i], OUTPUT);
    digitalWrite(relayPins[i], HIGH); // OFF (active LOW)
    Serial.print("        Relay ");
    Serial.print(i+1);
    Serial.print(" -> GPIO");
    Serial.println(relayPins[i]);
  }
  Serial.println("[RELAY] ✅ All relays initialized (OFF)");
}

void setRelay(int relayNum, bool state) {
  if(relayNum < 1 || relayNum > numRelays) return;
  
  // Active LOW: LOW = ON, HIGH = OFF
  digitalWrite(relayPins[relayNum - 1], state ? LOW : HIGH);
  
  Serial.print("[RELAY] Relay ");
  Serial.print(relayNum);
  Serial.print(" -> ");
  Serial.println(state ? "ON" : "OFF");
}

void setAllRelays(bool state) {
  Serial.print("[RELAY] All relays -> ");
  Serial.println(state ? "ON" : "OFF");
  
  for(int i = 0; i < numRelays; i++) {
    digitalWrite(relayPins[i], state ? LOW : HIGH);
  }
}

// ========== WEB SERVER HANDLERS ==========

// Handler untuk /relay
void handleRelay() {
  if(!server.hasArg("id") || !server.hasArg("action")) {
    server.send(400, "application/json", 
      "{\"error\":\"Missing parameters. Use: /relay?id=1&action=on\"}");
    return;
  }
  
  String id = server.arg("id");
  String action = server.arg("action");
  bool state = (action == "on");
  
  StaticJsonDocument<200> doc;
  
  if(id == "all") {
    setAllRelays(state);
    doc["status"] = "success";
    doc["relay"] = "all";
    doc["action"] = action;
  } else {
    int relayNum = id.toInt();
    if(relayNum >= 1 && relayNum <= numRelays) {
      setRelay(relayNum, state);
      doc["status"] = "success";
      doc["relay"] = relayNum;
      doc["action"] = action;
    } else {
      doc["status"] = "error";
      doc["message"] = "Invalid relay ID. Use 1-4 or 'all'";
      String response;
      serializeJson(doc, response);
      server.send(400, "application/json", response);
      return;
    }
  }
  
  String response;
  serializeJson(doc, response);
  server.send(200, "application/json", response);
  
  Serial.println("[API] Relay command executed: " + response);
}

// Handler untuk /info
void handleInfo() {
  Serial.println("[API] /info requested");
  
  // Baca data PZEM
  float voltage = pzem.voltage();
  float current = pzem.current();
  float power = pzem.power();
  float energy = pzem.energy();
  float frequency = pzem.frequency();
  float pf = pzem.pf();
  
  // Cek validitas data
  if(isnan(voltage) || voltage <= 0) {
    server.send(503, "application/json", 
      "{\"error\":\"PZEM sensor not responding\"}");
    return;
  }
  
  // ✅ FIX: Hitung power manual jika PZEM return 0 atau NaN
  if(isnan(power) || power <= 0) {
    if(!isnan(current) && current > 0.01) {
      power = voltage * current * 0.5; // Estimasi PF 0.5
    }
  }
  
  // Buat JSON response dengan pembulatan yang tepat
  StaticJsonDocument<256> doc;
  doc["device_id"] = device_id;
  doc["voltage"] = round(voltage * 10) / 10.0;        // 1 desimal
  doc["current"] = round(current * 1000) / 1000.0;    // 3 desimal
  doc["power"] = round(power * 10) / 10.0;            // 1 desimal
  doc["energy"] = round(energy * 100) / 100.0;        // 2 desimal
  doc["frequency"] = round(frequency * 10) / 10.0;    // 1 desimal
  doc["pf"] = round(pf * 100) / 100.0;                // 2 desimal
  doc["ip"] = WiFi.localIP().toString();
  doc["rssi"] = WiFi.RSSI();
  
  String response;
  serializeJson(doc, response);
  
  server.send(200, "application/json", response);
  
  Serial.println("[API] Response: " + response);
}

// Handler untuk root
void handleRoot() {
  String html = "<!DOCTYPE html><html><head><title>ESP32 PZEM Monitor</title>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<style>body{font-family:Arial;margin:20px;} ";
  html += "h1{color:#0066cc;} .info{background:#f0f0f0;padding:10px;margin:10px 0;border-radius:5px;} ";
  html += ".btn{padding:10px 20px;margin:5px;border:none;border-radius:5px;cursor:pointer;font-size:16px;} ";
  html += ".btn-on{background:#4CAF50;color:white;} .btn-off{background:#f44336;color:white;}</style></head>";
  html += "<body><h1>ESP32-C3 PZEM Web API</h1>";
  html += "<div class='info'><b>Device:</b> " + String(device_id) + "</div>";
  html += "<div class='info'><b>IP:</b> " + WiFi.localIP().toString() + "</div>";
  html += "<div class='info'><b>Port:</b> " + String(webServerPort) + "</div>";
  html += "<h2>API Endpoints:</h2>";
  html += "<div class='info'><b>GET /info</b> - Get PZEM sensor data</div>";
  html += "<div class='info'><b>GET /relay?id=1&action=on</b> - Control relay (id: 1-4 or 'all', action: on/off)</div>";
  html += "<h2>Relay Control:</h2>";
  
  for(int i = 1; i <= numRelays; i++) {
    html += "<div class='info'>Relay " + String(i) + ": ";
    html += "<button class='btn btn-on' onclick=\"fetch('/relay?id=" + String(i) + "&action=on')\">ON</button> ";
    html += "<button class='btn btn-off' onclick=\"fetch('/relay?id=" + String(i) + "&action=off')\">OFF</button></div>";
  }
  
  html += "<div class='info'>All Relays: ";
  html += "<button class='btn btn-on' onclick=\"fetch('/relay?id=all&action=on')\">ALL ON</button> ";
  html += "<button class='btn btn-off' onclick=\"fetch('/relay?id=all&action=off')\">ALL OFF</button></div>";
  html += "</body></html>";
  
  server.send(200, "text/html", html);
}

// ========== SETUP ==========
void setup() {
  Serial.begin(115200);
  delay(2000);
  
  Serial.println("\n\n========================================");
  Serial.println("  ESP32-C3 PZEM Web API");
  Serial.println("  With 4 Relay Control");
  Serial.println("========================================\n");
  
  // Setup LED
  Serial.println("[LED] Initializing FastLED...");
  FastLED.addLeds<WS2812, LED_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(50);
  leds[0] = CRGB::Black;
  FastLED.show();
  Serial.println("[LED] ✅ LED initialized on GPIO48");
  
  // Setup Relays
  setupRelays();
  
  // Stabilisasi power
  Serial.println("[SETUP] Power stabilization...");
  delay(VOLTAGE_STABILIZATION_DELAY);
  
  // Koneksi WiFi dengan LED merah berkedip
  Serial.println("[WiFi] Connecting...");
  WiFi.disconnect(true);
  delay(500);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.setTxPower(WIFI_POWER_11dBm);
  WiFi.begin(ssid, password);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 60) {
    ledBlinkRed(); // LED merah berkedip
    Serial.print(".");
    attempts++;
  }
  
  Serial.println();
  wifiConnected = (WiFi.status() == WL_CONNECTED);
  
  if (wifiConnected) {
    Serial.print("[WiFi] ✅ Connected! IP: ");
    Serial.println(WiFi.localIP());
    Serial.print("[WiFi] RSSI: ");
    Serial.println(WiFi.RSSI());
    
    // LED hijau berkedip 3x lalu mati
    ledBlinkGreen3Times();
    
  } else {
    Serial.println("[WiFi] ❌ Failed to connect!");
    // LED merah solid jika gagal
    leds[0] = CRGB::Red;
    FastLED.show();
  }
  
  // Setup NTP
  if (wifiConnected) {
    Serial.println("[NTP] Syncing time...");
    configTime(7 * 3600, 0, "pool.ntp.org", "time.google.com");
    delay(2000);
  }
  
  // Inisialisasi PZEM
  Serial.println("[PZEM] Initializing...");
  Serial.print("        RX: GPIO");
  Serial.println(PZEM_RX_PIN);
  Serial.print("        TX: GPIO");
  Serial.println(PZEM_TX_PIN);
  
  PZEMSerial.begin(9600, SERIAL_8N1, PZEM_RX_PIN, PZEM_TX_PIN);
  delay(1000);
  
  for (int i = 0; i < 5; i++) {
    float v = pzem.voltage();
    Serial.print("[PZEM] Test read ");
    Serial.print(i+1);
    Serial.print(": ");
    Serial.println(v);
    
    if (!isnan(v) && v > 0) {
      pzemConnected = true;
      Serial.println("[PZEM] ✅ Connected!");
      break;
    }
    delay(1000);
  }
  
  if (!pzemConnected) {
    Serial.println("[PZEM] ⚠ Not Connected! (Will retry in loop)");
  }
  
  // Setup Web Server
  if(wifiConnected) {
    Serial.println("[WEB] Starting web server...");
    Serial.print("      Port: ");
    Serial.println(webServerPort);
    
    server.on("/", handleRoot);
    server.on("/info", handleInfo);
    server.on("/relay", handleRelay);
    
    server.onNotFound([]() {
      server.send(404, "application/json", 
        "{\"error\":\"Not found. Available: /, /info, /relay\"}");
    });
    
    server.begin();
    Serial.println("[WEB] ✅ Server started!");
    Serial.print("      Access at: http://");
    Serial.print(WiFi.localIP());
    Serial.print(":");
    Serial.println(webServerPort);
  }
  
  Serial.println("\n[SETUP] ✅ Complete!\n");
  Serial.println("========================================");
  Serial.println("Available Endpoints:");
  Serial.println("- GET /         → Web interface");
  Serial.println("- GET /info     → PZEM sensor data (JSON)");
  Serial.println("- GET /relay?id=1&action=on  → Control relay");
  Serial.println("- GET /relay?id=all&action=off → Control all");
  Serial.println("========================================\n");
}

// ========== LOOP ==========
void loop() {
  // Handle web server requests
  if(wifiConnected) {
    server.handleClient();
  }
  
  // Cek WiFi reconnection
  if (WiFi.status() != WL_CONNECTED) {
    if (wifiConnected) {
      Serial.println("[WiFi] ⚠ Disconnected, reconnecting...");
      wifiConnected = false;
      ledStatusDone = false;
      
      // LED merah berkedip saat reconnecting
      while(WiFi.status() != WL_CONNECTED) {
        ledBlinkRed();
        delay(100);
      }
      
      wifiConnected = true;
      Serial.print("[WiFi] ✅ Reconnected! IP: ");
      Serial.println(WiFi.localIP());
      
      // LED hijau berkedip 3x
      ledBlinkGreen3Times();
    }
  }
  
  delay(10);
}