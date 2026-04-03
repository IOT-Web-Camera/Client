import asyncio
import websockets
import json
import os
import sys
import subprocess
import threading
import time
import requests
import pigpio
import math

# ─────────────────────────────────────────
# PARAMÈTRES MICRO
# ─────────────────────────────────────────

THRESH_FACTOR    = 4.0
MIN_DB           = 6.0
DEBOUNCE_COUNT   = 2
HOLD_SECONDS     = 3.0

CAL_SAMPLES = 200
CAL_DELAY   = 0.002
SAMPLES     = 50

_last_detection_time = 0.0
_consecutive = 0

baseline = 0
ambient_rms = 0

# ─────────────────────────────────────────
# SIMULATION ADC (À REMPLACER PAR TON VRAI CAPTEUR)
# ─────────────────────────────────────────

def read_adc():
    """⚠️ À remplacer par ton ADC réel (ADS1115 / MCP3008)"""
    return 0.5 + (0.02 * math.sin(time.time() * 10))  # simulation bruit

# ─────────────────────────────────────────
# RMS + DÉTECTION
# ─────────────────────────────────────────

def calibrate_baseline():
    global baseline, ambient_rms

    vals = []
    print(f"🔧 Calibration ({CAL_SAMPLES} samples)...")

    for _ in range(CAL_SAMPLES):
        v = read_adc()
        vals.append(v)
        time.sleep(CAL_DELAY)

    baseline = sum(vals) / len(vals)

    sq = [(x - baseline) ** 2 for x in vals]
    ambient_rms = math.sqrt(sum(sq) / len(sq))

    print(f"✅ Baseline={baseline:.4f} | RMS bruit={ambient_rms:.6f}")

def read_rms():
    sq_sum = 0.0

    for _ in range(SAMPLES):
        v = read_adc()
        dv = v - baseline
        sq_sum += dv * dv
        time.sleep(0.001)

    return math.sqrt(sq_sum / SAMPLES)

def is_peak_now():
    global _last_detection_time, _consecutive

    now = time.time()

    if now - _last_detection_time < HOLD_SECONDS:
        return False, 0.0

    rms = read_rms()

    rel_thresh = ambient_rms * THRESH_FACTOR
    db = 20.0 * math.log10(rms / max(ambient_rms, 1e-12)) if rms > 0 else -999

    if rms >= rel_thresh or db >= MIN_DB:
        _consecutive += 1
    else:
        _consecutive = 0

    if _consecutive >= DEBOUNCE_COUNT:
        _last_detection_time = now
        _consecutive = 0
        print("🔊 PEAK SONORE DÉTECTÉ !")
        return True, rms

    return False, rms

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────

def load_config():
    if not os.path.exists("config.json"):
        print("❌ config.json introuvable")
        sys.exit(1)
    return json.load(open("config.json"))

config = load_config()

VPS_IP        = config.get("server_url")
CAMERA_NAME   = config.get("camera_name")
STREAM_PASS   = config.get("stream_pass")
STREAM_USER   = config.get("stream_user", "admin")

WIDTH         = config.get("width", 640)
HEIGHT        = config.get("height", 360)
FRAMERATE     = config.get("framerate", 10)

RTSP_URL    = f"rtsp://{STREAM_USER}:{STREAM_PASS}@{VPS_IP}:8554/{CAMERA_NAME}"
LARAVEL_URL = f"http://{VPS_IP}/api/camera"
WS_URL      = f"ws://{VPS_IP}:8765"

# ─────────────────────────────────────────
# DHT11
# ─────────────────────────────────────────

import board
import adafruit_dht

dht_device = adafruit_dht.DHT11(board.D4)

def read_dht11():
    try:
        return dht_device.temperature, dht_device.humidity
    except:
        return None, None

# ─────────────────────────────────────────
# PIGPIO
# ─────────────────────────────────────────

pi = pigpio.pi()

# ─────────────────────────────────────────
# TELEMETRY
# ─────────────────────────────────────────

stop_event = threading.Event()

def send_event(event_type, payload=None):
    try:
        requests.post(
            f"{LARAVEL_URL}/event",
            json={
                "device": CAMERA_NAME,
                "type": event_type,
                "payload": payload or {}
            },
            timeout=3
        )
    except:
        pass

def telemetry_loop():
    while not stop_event.is_set():
        temp, hum = read_dht11()

        sound, rms = is_peak_now()

        # Option : convertir en dB (plus lisible)
        db = 20.0 * math.log10(rms / max(ambient_rms, 1e-12)) if rms > 0 else -60

        payload = {
            "temperature": temp,
            "humidity": hum,
            "sound_level": round(db, 2)  # 🔥 valeur continue
        }

        if sound:
            payload["sound"] = True

        print("📊", payload)

        send_event("telemetry", payload)

        if sound:
            send_event("sound_detected", {
                "level_db": round(db, 2)
            })

        time.sleep(5)

# ─────────────────────────────────────────
# STREAM
# ─────────────────────────────────────────

stream_process = None

def start_stream():
    global stream_process

    cmd = (
        f"rpicam-vid -t 0 --inline --nopreview "
        f"--width {WIDTH} --height {HEIGHT} --framerate {FRAMERATE} "
        f"-o - | ffmpeg -i - -c copy -f rtsp {RTSP_URL}"
    )

    stream_process = subprocess.Popen(cmd, shell=True)

def stream_watchdog():
    while not stop_event.is_set():
        if stream_process.poll() is not None:
            print("🔄 restart stream")
            start_stream()
        time.sleep(5)

# ─────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────

async def websocket_client():
    async for ws in websockets.connect(WS_URL):
        try:
            await ws.send(json.dumps({
                "device_id": CAMERA_NAME,
                "action": "REGISTER"
            }))

            async for msg in ws:
                print("📥", msg)

        except:
            await asyncio.sleep(5)

def run_ws():
    asyncio.run(websocket_client())

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    print("🎤 Initialisation micro...")
    calibrate_baseline()

    start_stream()

    threading.Thread(target=telemetry_loop, daemon=True).start()
    threading.Thread(target=stream_watchdog, daemon=True).start()
    threading.Thread(target=run_ws).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        if pi.connected:
            pi.stop()

if __name__ == "__main__":
    main()