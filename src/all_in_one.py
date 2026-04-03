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

# ─────────────────────────────────────────
# CHARGEMENT CONFIG
# ─────────────────────────────────────────

def load_config():
    config_path = "config.json"
    if not os.path.exists(config_path):
        print(f"❌ config.json introuvable")
        sys.exit(1)
    with open(config_path, "r") as f:
        return json.load(f)

config = load_config()

VPS_IP        = config.get("server_url")
CAMERA_NAME   = config.get("camera_name")
STREAM_PASS   = config.get("stream_pass")
STREAM_USER   = config.get("stream_user", "admin")
WIDTH         = config.get("width", 640)
HEIGHT        = config.get("height", 360)
FRAMERATE     = config.get("framerate", 10)

RTSP_URL      = f"rtsp://{STREAM_USER}:{STREAM_PASS}@{VPS_IP}:8554/{CAMERA_NAME}"
LARAVEL_URL   = f"http://{VPS_IP}/api/camera"
WS_URL        = f"ws://{VPS_IP}:8765"
HB_INTERVAL   = 25

# ─────────────────────────────────────────
# DHT11
# ─────────────────────────────────────────

import board
import adafruit_dht

dht_device = adafruit_dht.DHT11(board.D4, use_pulseio=True)

def read_dht11():
    for _ in range(3):
        try:
            temp = dht_device.temperature
            hum  = dht_device.humidity
            if temp is not None and hum is not None:
                return temp, hum
        except RuntimeError:
            time.sleep(1)
    return None, None

# ─────────────────────────────────────────
# LED EMBARQUÉE
# ─────────────────────────────────────────

def set_onboard_led(state):
    val = "1" if state == "ON" else "0"
    for path in ["/sys/class/leds/ACT/", "/sys/class/leds/led0/"]:
        if os.path.exists(path):
            os.system(f"echo none | tee {path}trigger > /dev/null 2>&1")
            os.system(f"echo {val} | tee {path}brightness > /dev/null 2>&1")
            print(f"💡 LED {'allumée' if state == 'ON' else 'éteinte'}")
            return
    print("⚠️ LED introuvable sur ce système")

# ─────────────────────────────────────────
# KY-038 (sortie D0 numérique)
# ─────────────────────────────────────────

KY038_GPIO = 17  # À adapter selon ton branchement
pi = pigpio.pi()

def read_ky038():
    """Retourne True si son détecté (D0 = LOW sur la plupart des modules)"""
    if pi.connected:
        return pi.read(KY038_GPIO) == 0
    return None

# ─────────────────────────────────────────
# HEARTBEAT LARAVEL
# ─────────────────────────────────────────

stop_event = threading.Event()

def heartbeat_loop():
    while not stop_event.is_set():
        try:
            requests.post(
                f"{LARAVEL_URL}/heartbeat",
                json={"path": CAMERA_NAME, "action": "publish"},
                timeout=3
            )
            print(f"💓 Heartbeat envoyé")
        except Exception as e:
            print(f"⚠️ Heartbeat échoué : {e}")
        stop_event.wait(HB_INTERVAL)

def send_event(event_type, payload=None):
    """Envoie un événement HTTP à Laravel"""
    try:
        requests.post(
            f"{LARAVEL_URL}/event",
            json={
                "device": CAMERA_NAME,
                "type":   event_type,
                "payload": payload or {}
            },
            timeout=3
        )
        print(f"📡 Event Laravel → {event_type}")
    except Exception as e:
        print(f"⚠️ Event Laravel échoué : {e}")

# ─────────────────────────────────────────
# STREAM VIDÉO (rpicam-vid + ffmpeg)
# ─────────────────────────────────────────

stream_process = None

def start_stream():
    global stream_process

    pipeline = (
        f"rpicam-vid -t 0 --inline --nopreview "
        f"--width {WIDTH} --height {HEIGHT} --framerate {FRAMERATE} "
        f"--codec h264 --profile baseline --level 4.1 "
        f"--intra 30 "
        f"--denoise off --sharpness 0 "
        f"-o - | "
        f"ffmpeg -fflags nobuffer -flags low_delay -analyzeduration 0 -probesize 32 "
        f"-i - -c copy -f rtsp -rtsp_transport udp {RTSP_URL}"
    )

    print(f"🎥 Démarrage du flux → {RTSP_URL}")
    stream_process = subprocess.Popen(pipeline, shell=True)

def stop_stream():
    global stream_process
    if stream_process:
        stream_process.terminate()
        try:
            stream_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stream_process.kill()
        stream_process = None
        print("🛑 Flux arrêté")

def stream_watchdog():
    """Redémarre le stream s'il plante"""
    while not stop_event.is_set():
        if stream_process is None or stream_process.poll() is not None:
            print("🔄 Redémarrage du flux...")
            stop_stream()
            time.sleep(2)
            start_stream()
        time.sleep(5)

# ─────────────────────────────────────────
# TÉLÉMÉTRIE (DHT11 + KY-038 → Laravel)
# ─────────────────────────────────────────

def telemetry_loop():
    while not stop_event.is_set():
        temp, hum = read_dht11()
        son = read_ky038()

        payload = {
            "temperature": temp,
            "humidity":    hum,
            "sound":       son,
        }

        print(f"📊 Télémétrie → {payload}")
        send_event("telemetry", payload)
        stop_event.wait(10)

# ─────────────────────────────────────────
# WEBSOCKET (commandes depuis le bridge)
# ─────────────────────────────────────────

ws_ref  = None
ws_loop = None

async def websocket_client():
    global ws_ref, ws_loop
    ws_loop = asyncio.get_event_loop()

    print(f"🔌 Connexion au bridge WebSocket → {WS_URL}")
    async for websocket in websockets.connect(WS_URL):
        try:
            ws_ref = websocket
            await websocket.send(json.dumps({
                "device_id": CAMERA_NAME,
                "action":    "REGISTER"
            }))
            print(f"✅ Enregistré : {CAMERA_NAME}")

            async for message in websocket:
                try:
                    data    = json.loads(message)
                    action  = data.get("action") or data.get("command", "")
                    payload = data.get("payload", {})

                    print(f"📥 Commande reçue : {action}")

                    if action == "REGISTER_OK":
                        print("✅ Enregistrement confirmé")

                    elif action == "CMD_LED":
                        set_onboard_led(payload.get("state", "OFF"))

                    elif action == "CMD_MOVE":
                        print(f"🕹️ MOVE → {payload.get('direction')}")
                        # À brancher sur tes servos

                    elif action == "CMD_REBOOT":
                        print("🔄 Reboot demandé")
                        stop_event.set()
                        os.system("reboot")

                    elif action == "CMD_STREAM_RESTART":
                        stop_stream()
                        time.sleep(1)
                        start_stream()

                    else:
                        print(f"❓ Commande inconnue : {action}")

                except json.JSONDecodeError:
                    pass

        except websockets.ConnectionClosed:
            ws_ref = None
            print("⚠️ Bridge déconnecté, reconnexion dans 5s...")
            await asyncio.sleep(5)

def run_websocket_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(websocket_client())
    finally:
        loop.close()

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    print(f"\n🚀 Démarrage — caméra : {CAMERA_NAME}\n")

    threads = [
        threading.Thread(target=heartbeat_loop,    daemon=True,  name="Heartbeat"),
        threading.Thread(target=telemetry_loop,    daemon=True,  name="Telemetry"),
        threading.Thread(target=stream_watchdog,   daemon=True,  name="Watchdog"),
        threading.Thread(target=run_websocket_thread, daemon=False, name="WebSocket"),
    ]

    start_stream()

    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arrêt...")
        stop_event.set()
        stop_stream()
        try:
            requests.post(
                f"{LARAVEL_URL}/heartbeat",
                json={"path": CAMERA_NAME, "action": "unpublish"},
                timeout=3
            )
        except Exception:
            pass
        if pi.connected:
            pi.stop()
        print("👋 Arrêt propre.")

if __name__ == "__main__":
    main()