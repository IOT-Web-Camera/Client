import cv2
import subprocess
import numpy as np
import datetime
import threading
import time
import os
import requests
import asyncio
import websockets
import json
import sys

# --- CORRECTIF PATH FFMPEG ---
FFMPEG_DIR = r"C:/Users/defra/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.0.1-full_build/bin"
os.environ["PATH"] += os.pathsep + FFMPEG_DIR

# --- CONFIG SERVEUR ---
LARAVEL_URL  = "http://51.210.11.74/api/camera/heartbeat"
BRIDGE_URL   = "ws://51.210.11.74:8765"
HEARTBEAT_INTERVAL = 25

# --- CONFIGURATION DES FLUX ---
CAMERAS_CONFIG = [
    {
        "name": "sodium_5f3a",
        "label": "Entrée Principale",
        "url": "rtsp://admin:1234@51.210.11.74:8554/sodium_5f3a",
        "color": (0, 255, 0),
        "size": (1280, 720)
    },
    {
        "name": "sodium_test_2",
        "label": "Parking Arrière",
        "url": "rtsp://admin:1234@51.210.11.74:8554/sodium_test_2",
        "color": (0, 0, 255),
        "size": (800, 600)
    }
]


class FakeCamera:
    def __init__(self, config):
        self.name   = config["name"]
        self.label  = config["label"]
        self.url    = config["url"]
        self.color  = config["color"]
        self.width, self.height = config["size"]
        self.ws = None
        self.loop = None

        self.fps = 30
        self.stop_event = threading.Event()
        self.process = None

        self.ws_thread = None
        self.hb_thread = None
        self.stream_thread = None

    # --- Heartbeat ---

    def send_heartbeat(self, action="publish"):
        try:
            requests.post(
                LARAVEL_URL,
                json={"path": self.name, "action": action},
                timeout=3
            )
            print(f"💓 [{self.name}] Heartbeat [{action}]", flush=True)
        except Exception as e:
            print(f"⚠️  [{self.name}] Heartbeat échoué : {e}", flush=True)

    def heartbeat_loop(self):
        while not self.stop_event.is_set():
            self.send_heartbeat("publish")
            self.stop_event.wait(HEARTBEAT_INTERVAL)

    # --- WebSocket ---

    async def websocket_client(self):
        """Client WebSocket pour recevoir les commandes du bridge"""
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(BRIDGE_URL) as ws:
                    self.ws = ws
                    self.loop = asyncio.get_event_loop()
                    print(f"🔌 [{self.name}] Connecté au bridge", flush=True)


                    # Enregistrement
                    await ws.send(json.dumps({
                        "device_id": self.name,
                        "action": "REGISTER"
                    }))

                    async for message in ws:
                        print(f"📥 Message brut reçu : {message}")
                        try:
                            data    = json.loads(message)
                            action  = data.get("action") or data.get("command", "")
                            payload = data.get("payload", {})

                            if action == "REGISTER_OK":
                                print(f"✅ [{self.name}] Enregistrement confirmé", flush=True)

                            elif action == "CMD_LED":
                                state = payload.get("state", "?")
                                print(f"🎮 [{self.name}] LED → {state}", flush=True)

                            elif action == "CMD_MOVE":
                                direction = payload.get("direction", "?")
                                print(f"🎮 [{self.name}] MOVE → {direction}", flush=True)

                            elif action == "CMD_REBOOT":
                                print(f"🔄 [{self.name}] REBOOT demandé", flush=True)

                            else:
                                print(f"❓ [{self.name}] Commande inconnue : {action}", flush=True)

                        except json.JSONDecodeError:
                            pass

            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"⚠️  [{self.name}] Bridge déconnecté ({e}), reconnexion dans 5s...", flush=True)
                    await asyncio.sleep(5)

    def run_websocket_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.websocket_client())
        finally:
            loop.close()

    # --- Stream vidéo / FFmpeg ---

    def start_ffmpeg(self):
        command = [
            'ffmpeg', '-y',
            '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-pix_fmt', 'bgr24', '-s', f"{self.width}x{self.height}",
            '-r', str(self.fps), '-i', '-',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-preset', 'ultrafast', '-tune', 'zerolatency',
            '-f', 'rtsp', self.url
        ]

        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        print(f"🚀 [{self.name}] Flux démarré → {self.url}", flush=True)
        self.send_heartbeat("publish")

    def run_stream(self):
        try:
            self.start_ffmpeg()

            while not self.stop_event.is_set():
                frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                t  = datetime.datetime.now().timestamp()
                cx = int(self.width / 2 + (self.width / 4) * np.sin(t * 2))
                cv2.circle(frame, (cx, self.height // 2), 40, self.color, -1)

                ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cv2.putText(frame, f"CAM: {self.label}", (30, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
                cv2.putText(frame, f"TIME: {ts}", (30, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 200, 200), 2)

                try:
                    self.process.stdin.write(frame.tobytes())
                except Exception as e:
                    print(f"❌ [{self.name}] Erreur écriture FFmpeg : {e}", flush=True)
                    break

                time.sleep(1 / self.fps)

        except Exception as e:
            print(f"❌ [{self.name}] Erreur stream : {e}", flush=True)
        finally:
            self.stop()

    # --- Gestion de vie ---

    def start(self):
        # Thread WebSocket (non-daemon pour rester vivant)
        self.ws_thread = threading.Thread(
            target=self.run_websocket_thread,
            name=f"WS-{self.name}",
            daemon=False
        )
        self.ws_thread.start()

        # Thread Heartbeat (daemon ok)
        self.hb_thread = threading.Thread(
            target=self.heartbeat_loop,
            name=f"HB-{self.name}",
            daemon=True
        )
        self.hb_thread.start()

        # Thread Stream (daemon ok, mais on garde le main vivant)
        self.stream_thread = threading.Thread(
            target=self.run_stream,
            name=f"STREAM-{self.name}",
            daemon=True
        )
        self.stream_thread.start()

        # Thread Télémetrie
        self.tel_thread = threading.Thread(
            target=self.telemetry_loop,
            name=f"TEL-{self.name}",
            daemon=True
        )
        self.tel_thread.start()

    def stop(self):
        if not self.stop_event.is_set():
            print(f"🛑 [{self.name}] Arrêt demandé", flush=True)
        self.stop_event.set()

        try:
            self.send_heartbeat("unpublish")
        except Exception:
            pass

        if self.process and self.process.stdin:
            try:
                self.process.stdin.close()
            except Exception:
                pass

        if self.process:
            try:
                self.process.wait(timeout=3)
            except Exception:
                self.process.kill()

    def send_event(self, event_type, payload=None):
        if payload is None:
            payload = {}

        # Envoi WebSocket (bridge)
        if self.ws and self.loop:
            try:
                msg = {
                    "type": event_type,
                    "device": self.name,
                    "payload": payload
                }
                asyncio.run_coroutine_threadsafe(
                    self.ws.send(json.dumps(msg)),
                    self.loop
                )
                print(f"📨 [{self.name}] Event envoyé au bridge → {msg}", flush=True)
            except Exception as e:
                print(f"⚠️ [{self.name}] Impossible d'envoyer l'event WS : {e}", flush=True)

        # Envoi HTTP vers Laravel
        try:
            requests.post(
                "http://51.210.11.74/api/camera/event",
                json={
                    "device": self.name,
                    "type": event_type,
                    "payload": payload
                },
                timeout=3
            )
            print(f"📡 [{self.name}] Event envoyé à Laravel → {event_type}", flush=True)
        except Exception as e:
            print(f"⚠️ [{self.name}] Laravel event échoué : {e}", flush=True)

    def telemetry_loop(self):
        """Envoie régulièrement des données de télémetrie au bridge"""
        while not self.stop_event.is_set():
            telemetry = {
                "temperature": round(20 + np.random.random() * 5, 2),
                "battery": max(0, 100 - int(time.time() % 100)),  # batterie fictive
                "signal": -60 + int(np.random.random() * 10),  # signal fictif
                "uptime": int(time.time())
            }

            self.send_event("telemetry", telemetry)
            time.sleep(5)


# --- LANCEMENT GLOBAL ---

def main():
    cameras = [FakeCamera(cfg) for cfg in CAMERAS_CONFIG]

    for cam in cameras:
        cam.start()

    print("\n📡 Tous les flux sont lancés. Appuyez sur Ctrl+C pour tout arrêter.\n", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arrêt de tous les flux...", flush=True)
        for cam in cameras:
            cam.stop()
        time.sleep(2)


if __name__ == "__main__":
    main()
