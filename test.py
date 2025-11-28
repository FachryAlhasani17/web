from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import cv2
import joblib
import numpy as np
import time
import datetime
import warnings
import json
import asyncio
from typing import Optional

warnings.filterwarnings("ignore")

app = FastAPI(title="Sistem Kontrol Kamar")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# KONFIGURASI SISTEM
# ==========================================
TIMER_DURASI = 30
PROB_THRESHOLD = 0.80
IMG_SIZE = (100, 100)
WINDOW_SIZE = (150, 250)
STEP_SIZE = (80, 80)
DARK_THRESHOLD = 40
JEDA_DETEKSI_DETIK = 1.0

# ==========================================
# GLOBAL VARIABLES
# ==========================================
svm_model = None
scaler = None
cap = None
last_detection_time = datetime.datetime.now()
relay_status = "ON"
ada_orang_cache = False
system_stats = {
    "prob_orang": 0.0,
    "prob_non": 0.0,
    "status": "KOSONG",
    "relay": "ON",
    "sisa_waktu": TIMER_DURASI
}

# ==========================================
# FUNGSI EKSTRAKSI FITUR
# ==========================================
def extract_hsv_features(image):
    img_resized = cv2.resize(image, IMG_SIZE)
    hsv_img = cv2.cvtColor(img_resized, cv2.COLOR_BGR2HSV)
    
    # Statistik warna (6 fitur)
    mean, std = cv2.meanStdDev(hsv_img)
    stat_features = np.concatenate([mean.flatten(), std.flatten()])
    
    # Histogram warna (96 fitur)
    hist_h = cv2.calcHist([hsv_img], [0], None, [32], [0, 180])
    hist_s = cv2.calcHist([hsv_img], [1], None, [32], [0, 256])
    hist_v = cv2.calcHist([hsv_img], [2], None, [32], [0, 256])
    
    cv2.normalize(hist_h, hist_h)
    cv2.normalize(hist_s, hist_s)
    cv2.normalize(hist_v, hist_v)
    
    hist_features = np.concatenate([hist_h.flatten(), hist_s.flatten(), hist_v.flatten()])
    final_features = np.concatenate([stat_features, hist_features])
    
    return final_features

def sliding_window(image, stepSize, windowSize):
    for y in range(0, image.shape[0] - windowSize[1], stepSize[1]):
        for x in range(0, image.shape[1] - windowSize[0], stepSize[0]):
            yield (x, y, image[y:y + windowSize[1], x:x + windowSize[0]])

# ==========================================
# LOAD MODEL
# ==========================================
@app.on_event("startup")
async def startup_event():
    global svm_model, scaler, cap
    try:
        svm_model = joblib.load("svm_model_hsv.pkl")
        scaler = joblib.load("scaler_hsv.pkl")
        print("✅ Model berhasil dimuat")
    except FileNotFoundError:
        print("❌ ERROR: File .pkl tidak ditemukan!")
        return
    
    cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        print("❌ ERROR: Webcam tidak terdeteksi!")

@app.on_event("shutdown")
async def shutdown_event():
    global cap
    if cap:
        cap.release()

# ==========================================
# FUNGSI DETEKSI
# ==========================================
last_check_time = time.time()

def process_frame(frame):
    global last_detection_time, relay_status, ada_orang_cache, last_check_time, system_stats
    
    current_time = time.time()
    display_frame = frame.copy()
    
    # Proses deteksi setiap interval
    if current_time - last_check_time > JEDA_DETEKSI_DETIK:
        last_check_time = current_time
        detected_now = False
        max_prob = 0.0
        
        for (x, y, window) in sliding_window(frame, STEP_SIZE, WINDOW_SIZE):
            if np.mean(window) < DARK_THRESHOLD:
                continue
            
            features = extract_hsv_features(window)
            features_scaled = scaler.transform(features.reshape(1, -1))
            prediction = svm_model.predict(features_scaled)[0]
            probability = svm_model.predict_proba(features_scaled)[0]
            conf_person = probability[1]
            
            if conf_person > max_prob:
                max_prob = conf_person
            
            if prediction == 1 and conf_person > PROB_THRESHOLD:
                detected_now = True
                # Gambar kotak deteksi
                cv2.rectangle(display_frame, (x, y), (x + WINDOW_SIZE[0], y + WINDOW_SIZE[1]), 
                            (0, 255, 0), 2)
                break
        
        ada_orang_cache = detected_now
        system_stats["prob_orang"] = max_prob * 100
        system_stats["prob_non"] = (1.0 - max_prob) * 100
    
    # Logika kontrol timer & relay
    waktu_sekarang = datetime.datetime.now()
    
    if ada_orang_cache:
        last_detection_time = waktu_sekarang
        if relay_status == "OFF":
            relay_status = "ON"
            print(f"\n🟢 ORANG MASUK! Relay ON")
        
        status_text = "STATUS: ADA ORANG"
        status_color = (0, 255, 0)
        system_stats["status"] = "ADA ORANG"
        system_stats["sisa_waktu"] = TIMER_DURASI
    else:
        elapsed = (waktu_sekarang - last_detection_time).total_seconds()
        sisa_waktu = TIMER_DURASI - elapsed
        
        if relay_status == "ON":
            if sisa_waktu > 0:
                status_text = f"KOSONG - Mati dalam {int(sisa_waktu)}s"
                status_color = (0, 165, 255)
                system_stats["status"] = "KOSONG"
                system_stats["sisa_waktu"] = int(sisa_waktu)
            else:
                relay_status = "OFF"
                print(f"\n🔴 WAKTU HABIS! Relay OFF")
                status_text = "LISTRIK MATI (HEMAT ENERGI)"
                status_color = (0, 0, 255)
                system_stats["status"] = "MATI"
                system_stats["sisa_waktu"] = 0
        else:
            status_text = "SYSTEM OFF"
            status_color = (0, 0, 255)
            system_stats["status"] = "MATI"
            system_stats["sisa_waktu"] = 0
    
    system_stats["relay"] = relay_status
    
    # Tampilan di frame
    cv2.putText(display_frame, status_text, (20, 40), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
    cv2.putText(display_frame, f"Orang: {system_stats['prob_orang']:.1f}%", (20, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    
    return display_frame

# ==========================================
# ENDPOINTS
# ==========================================
def generate_frames():
    while True:
        success, frame = cap.read()
        if not success:
            break
        
        processed_frame = process_frame(frame)
        ret, buffer = cv2.imencode('.jpg', processed_frame)
        frame_bytes = buffer.tobytes()
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(generate_frames(), 
                           media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/stats")
async def get_stats():
    return system_stats

@app.post("/reset_timer")
async def reset_timer():
    global last_detection_time
    last_detection_time = datetime.datetime.now()
    return {"message": "Timer direset", "status": "success"}

@app.post("/toggle_relay")
async def toggle_relay():
    global relay_status
    relay_status = "ON" if relay_status == "OFF" else "OFF"
    return {"relay_status": relay_status}

@app.get("/", response_class=HTMLResponse)
async def get_interface():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sistem Kontrol Kamar</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                overflow: hidden;
            }
            .header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
            }
            .header h1 {
                font-size: 2.5em;
                margin-bottom: 10px;
            }
            .header p {
                opacity: 0.9;
                font-size: 1.1em;
            }
            .content {
                padding: 30px;
            }
            .video-container {
                position: relative;
                background: #000;
                border-radius: 15px;
                overflow: hidden;
                margin-bottom: 30px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            }
            .video-container img {
                width: 100%;
                height: auto;
                display: block;
            }
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            .stat-card {
                background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                padding: 25px;
                border-radius: 15px;
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                transition: transform 0.3s;
            }
            .stat-card:hover {
                transform: translateY(-5px);
            }
            .stat-label {
                font-size: 0.9em;
                color: #666;
                margin-bottom: 10px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            .stat-value {
                font-size: 2em;
                font-weight: bold;
                color: #333;
            }
            .status-active { color: #10b981; }
            .status-warning { color: #f59e0b; }
            .status-danger { color: #ef4444; }
            .controls {
                display: flex;
                gap: 15px;
                justify-content: center;
                flex-wrap: wrap;
            }
            .btn {
                padding: 15px 30px;
                border: none;
                border-radius: 10px;
                font-size: 1em;
                font-weight: bold;
                cursor: pointer;
                transition: all 0.3s;
                box-shadow: 0 5px 15px rgba(0,0,0,0.2);
            }
            .btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 7px 20px rgba(0,0,0,0.3);
            }
            .btn-primary {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
            }
            .btn-success {
                background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                color: white;
            }
            .progress-bar {
                width: 100%;
                height: 30px;
                background: #e5e7eb;
                border-radius: 15px;
                overflow: hidden;
                margin-top: 10px;
            }
            .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #10b981 0%, #059669 100%);
                transition: width 0.3s;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🏠 Sistem Kontrol Kamar Otomatis</h1>
                <p>Deteksi Orang dengan AI & Hemat Energi</p>
            </div>
            
            <div class="content">
                <div class="video-container">
                    <img id="videoStream" src="/video_feed" alt="Video Stream">
                </div>
                
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">Status Ruangan</div>
                        <div class="stat-value" id="status">LOADING...</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Relay Status</div>
                        <div class="stat-value" id="relay">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Probabilitas Orang</div>
                        <div class="stat-value" id="probOrang">0%</div>
                        <div class="progress-bar">
                            <div class="progress-fill" id="progOrang"></div>
                        </div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Sisa Waktu</div>
                        <div class="stat-value" id="sisaWaktu">-</div>
                    </div>
                </div>
                
                <div class="controls">
                    <button class="btn btn-primary" onclick="resetTimer()">🔄 Reset Timer</button>
                    <button class="btn btn-success" onclick="toggleRelay()">⚡ Toggle Relay</button>
                </div>
            </div>
        </div>
        
        <script>
            async function updateStats() {
                try {
                    const response = await fetch('/stats');
                    const data = await response.json();
                    
                    // Update status
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = data.status;
                    statusEl.className = 'stat-value ' + 
                        (data.status === 'ADA ORANG' ? 'status-active' : 
                         data.status === 'KOSONG' ? 'status-warning' : 'status-danger');
                    
                    // Update relay
                    document.getElementById('relay').textContent = data.relay;
                    
                    // Update probabilitas
                    document.getElementById('probOrang').textContent = 
                        data.prob_orang.toFixed(1) + '%';
                    document.getElementById('progOrang').style.width = 
                        data.prob_orang + '%';
                    
                    // Update sisa waktu
                    document.getElementById('sisaWaktu').textContent = 
                        data.sisa_waktu + 's';
                    
                } catch (error) {
                    console.error('Error fetching stats:', error);
                }
            }
            
            async function resetTimer() {
                await fetch('/reset_timer', { method: 'POST' });
                alert('Timer berhasil direset!');
            }
            
            async function toggleRelay() {
                const response = await fetch('/toggle_relay', { method: 'POST' });
                const data = await response.json();
                alert('Relay: ' + data.relay_status);
            }
            
            // Update stats setiap 500ms
            setInterval(updateStats, 500);
            updateStats();
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)