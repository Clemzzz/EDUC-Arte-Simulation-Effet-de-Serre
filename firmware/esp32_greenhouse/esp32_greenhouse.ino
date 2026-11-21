/*
 * EDUC'Arte — Station de mesure d'effet de serre (ESP32 + DHT22/DHT11)
 *
 * Expérience de Terminale :
 *   Un capteur de température et d'humidité est placé sous une cloche éclairée
 *   par une lampe (simulant le soleil) en présence d'un papier humide.
 *   L'ESP32 héberge une interface web temps réel et permet l'export CSV.
 *
 * Bibliothèques Arduino requises :
 *   - Support carte ESP32 (Espressif)
 *   - DHT sensor library (Adafruit) + Adafruit Unified Sensor
 *
 * Câblage par défaut (DHT22 / DHT11) :
 *   VCC  -> 3.3V
 *   GND  -> GND
 *   DATA -> GPIO 4 (avec résistance pull-up 10 kOhm vers 3.3V si capteur nu)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DHT.h>

// ===================== CONFIGURATION =====================
const char* WIFI_SSID     = "VOTRE_SSID_WIFI";
const char* WIFI_PASSWORD = "VOTRE_MOT_DE_PASSE";

#define DHT_PIN            4
#define DHT_TYPE           DHT22   // Changer en DHT11 si vous utilisez un DHT11
#define SIMULATION_MODE    0       // Mettre à 1 pour tester sur ESP32 sans capteur branché

const uint32_t SAMPLE_INTERVAL_MS = 2000;  // 1 mesure toutes les 2 secondes
const size_t   MAX_SAMPLES        = 900;   // 30 minutes à 1 mesure / 2 s
// =========================================================

struct Sample {
  uint32_t elapsed_s;
  float temperature_c;
  float humidity_pct;
};

DHT dht(DHT_PIN, DHT_TYPE);
WebServer server(80);

Sample history[MAX_SAMPLES];
size_t sampleCount = 0;
size_t writeIndex  = 0;

uint32_t startMillis      = 0;
uint32_t lastSampleMillis = 0;

// État utilisé uniquement si SIMULATION_MODE == 1
float simTempC = 21.0f;
float simHumPct = 48.0f;

const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>EDUC'Arte — Effet de serre</title>
  <style>
    *{box-sizing:border-box}
    body{font-family:system-ui,-apple-system,sans-serif;background:#f0fdf4;color:#14532d;margin:0;padding:32px 16px}
    .wrap{max-width:720px;margin:0 auto;display:flex;flex-direction:column;gap:20px}
    .eyebrow{font-size:.76rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#15803d}
    h1{margin:4px 0 2px;font-size:1.85rem;letter-spacing:-.02em}
    .sub{color:#475569;margin:0;font-size:.95rem}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}
    .card{background:#fff;border:1px solid #d4e8dc;border-radius:14px;padding:18px 20px;box-shadow:0 2px 10px rgba(20,83,45,.05)}
    .lbl{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:#475569;font-weight:700}
    .val{font-size:2.2rem;font-weight:700;margin-top:10px;font-variant-numeric:tabular-nums}
    .t{color:#c2410c}.h{color:#0369a1}.u{font-size:1rem;color:#475569;margin-left:4px}
    .btn{display:flex;align-items:center;justify-content:center;min-height:48px;background:#15803d;
         color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:700}
    .btn:hover{background:#166534}
    .btn:focus-visible{outline:2px solid #15803d;outline-offset:2px}
  </style>
</head>
<body>
  <main class="wrap">
    <header>
      <div class="eyebrow">Station ESP32 • Cloche expérimentale</div>
      <h1>EDUC'Arte</h1>
      <p class="sub">Suivi en temps réel de l'effet de serre</p>
    </header>
    <section class="grid" aria-label="Mesures en temps réel">
      <article class="card"><div class="lbl">Température</div><div class="val t"><span id="t">--.-</span><span class="u">°C</span></div></article>
      <article class="card"><div class="lbl">Humidité</div><div class="val h"><span id="h">--.-</span><span class="u">%</span></div></article>
      <article class="card"><div class="lbl">Temps écoulé</div><div class="val" id="e">00:00</div></article>
    </section>
    <a class="btn" href="/api/export.csv" download="educarte_effet_de_serre.csv">Télécharger les données (.csv)</a>
  </main>
  <script>
    function fmt(s){
      const m=String(Math.floor(s/60)).padStart(2,'0'),sec=String(Math.floor(s%60)).padStart(2,'0');
      return m+':'+sec;
    }
    async function poll(){
      try{
        const r=await fetch('/api/data'); const d=await r.json();
        document.getElementById('t').textContent=d.temperature_c.toFixed(1);
        document.getElementById('h').textContent=d.humidity_pct.toFixed(1);
        document.getElementById('e').textContent=fmt(d.elapsed_s);
      }catch(e){}
    }
    poll(); setInterval(poll,2000);
  </script>
</body>
</html>
)rawliteral";

size_t getOldestIndex() {
  return (sampleCount < MAX_SAMPLES) ? 0 : writeIndex;
}

const Sample& getSampleAt(size_t logicalIndex) {
  return history[(getOldestIndex() + logicalIndex) % MAX_SAMPLES];
}

void recordMeasurement() {
  float temperature = NAN;
  float humidity = NAN;

#if SIMULATION_MODE
  const float dt = SAMPLE_INTERVAL_MS / 1000.0f;
  simTempC += (38.8f - simTempC) * (dt / 240.0f);
  simHumPct += (90.0f - simHumPct) * (dt / 180.0f);
  temperature = simTempC + ((float)random(-10, 11) * 0.01f);
  humidity    = simHumPct + ((float)random(-20, 21) * 0.01f);
#else
  temperature = dht.readTemperature();
  humidity    = dht.readHumidity();
#endif

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("[AVERTISSEMENT] Échec de lecture du capteur DHT.");
    return;
  }

  Sample s;
  s.elapsed_s     = (millis() - startMillis) / 1000;
  s.temperature_c = temperature;
  s.humidity_pct  = humidity;

  history[writeIndex] = s;
  writeIndex = (writeIndex + 1) % MAX_SAMPLES;
  if (sampleCount < MAX_SAMPLES) {
    sampleCount++;
  }

  Serial.printf("[t=%lus] Temp: %.1f C | Hum: %.1f %%\n",
                (unsigned long)s.elapsed_s, s.temperature_c, s.humidity_pct);
}

void handleRoot() {
  server.send_P(200, "text/html; charset=utf-8", INDEX_HTML);
}

void handleApiData() {
  if (sampleCount == 0) {
    server.send(503, "application/json", "{\"error\":\"Aucune mesure disponible\"}");
    return;
  }

  const Sample& latest = getSampleAt(sampleCount - 1);
  String json = "{";
  json += "\"mode\":\"";
  json += (SIMULATION_MODE ? "simulation" : "hardware");
  json += "\",\"sensor\":\"";
  json += (DHT_TYPE == DHT11 ? "DHT11" : "DHT22");
  json += "\",\"elapsed_s\":";
  json += String(latest.elapsed_s);
  json += ",\"temperature_c\":";
  json += String(latest.temperature_c, 1);
  json += ",\"humidity_pct\":";
  json += String(latest.humidity_pct, 1);
  json += ",\"sample_count\":";
  json += String(sampleCount);
  json += "}";

  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json; charset=utf-8", json);
}

void handleApiHistory() {
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json; charset=utf-8", "");

  server.sendContent(SIMULATION_MODE ? "{\"mode\":\"simulation\",\"samples\":[" : "{\"mode\":\"hardware\",\"samples\":[");
  for (size_t i = 0; i < sampleCount; i++) {
    const Sample& s = getSampleAt(i);
    String item = (i > 0 ? "," : "");
    item += "{\"elapsed_s\":";
    item += String(s.elapsed_s);
    item += ",\"temperature_c\":";
    item += String(s.temperature_c, 1);
    item += ",\"humidity_pct\":";
    item += String(s.humidity_pct, 1);
    item += "}";
    server.sendContent(item);
  }
  server.sendContent("]}");
  server.sendContent("");
}

void handleExportCsv() {
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.sendHeader("Content-Disposition", "attachment; filename=\"educarte_effet_de_serre.csv\"");
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "text/csv; charset=utf-8", "");

  server.sendContent("elapsed_s,temperature_c,humidity_pct\n");
  for (size_t i = 0; i < sampleCount; i++) {
    const Sample& s = getSampleAt(i);
    String line = String(s.elapsed_s) + "," +
                  String(s.temperature_c, 1) + "," +
                  String(s.humidity_pct, 1) + "\n";
    server.sendContent(line);
  }
  server.sendContent("");
}

void setup() {
  Serial.begin(115200);
  delay(200);

  Serial.println("\n=== EDUC'Arte — Station Effet de Serre (ESP32) ===");

#if !SIMULATION_MODE
  dht.begin();
#endif

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connexion au Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Connecté ! Adresse IP : http://");
  Serial.println(WiFi.localIP());

  startMillis = millis();
  lastSampleMillis = startMillis;
  recordMeasurement();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/data", HTTP_GET, handleApiData);
  server.on("/api/history", HTTP_GET, handleApiHistory);
  server.on("/api/export.csv", HTTP_GET, handleExportCsv);
  server.begin();
  Serial.println("Serveur HTTP démarré sur le port 80.");
}

void loop() {
  server.handleClient();

  uint32_t now = millis();
  if (now - lastSampleMillis >= SAMPLE_INTERVAL_MS) {
    lastSampleMillis = now;
    recordMeasurement();
  }
}
