import cv2
import subprocess
import numpy as np
import datetime
import threading
import time
import os

# --- CORRECTIF PATH FFMPEG ---
FFMPEG_DIR = r"C:/Users/defra/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.0.1-full_build/bin"
os.environ["PATH"] += os.pathsep + FFMPEG_DIR

# --- CONFIGURATION DES FLUX ---
# Tu peux ajouter autant de caméras que tu veux ici
CAMERAS = [
    {
        "name": "sodium_5f3a",
        "label": "Entrée Principale",
        "url": "rtsp://admin:testpass123@51.210.11.74:8554/sodium_5f3a",
        "color": (0, 255, 0),  # Vert
        "size": (1280, 720)
    },
    {
        "name": "sodium_test_2",
        "label": "Parking Arrière",
        "url": "rtsp://admin:testpass123@51.210.11.74:8554/sodium_test_2",
        "color": (0, 0, 255),  # Rouge
        "size": (800, 600)
    },
    {
        "name": "sodium_test_3",
        "label" : "Surveillance system",
        "url": "rtsp://admin:1234@51.210.11.74:8554/sodium_test_3",
        "color": (255, 0, 0),  # Bleu
        "size": (1920, 1080)
    },
    {
        "name": "stef_cam",
        "label" : "Surveillance system",
        "url": "rtsp://admin:1234@51.210.11.74:8554/stef_cam",
        "color": (255, 0, 0),  # Bleu
        "size": (1920, 1080)
    }
]


def start_camera_stream(config):
    width, height = config["size"]
    fps = 30

    command = [
        'ffmpeg',
        '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-pix_fmt', 'bgr24',
        '-s', f"{width}x{height}",
        '-r', str(fps),
        '-i', '-',
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-preset', 'ultrafast',
        '-tune', 'zerolatency',  # Important pour le temps réel
        '-f', 'rtsp',
        config["url"]
    ]

    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    print(f"🚀 Flux démarré : {config['label']} -> {config['url']}")

    try:
        while True:
            # 1. Création de l'image
            frame = np.zeros((height, width, 3), dtype=np.uint8)

            # 2. Animation (cercle qui bouge)
            t = datetime.datetime.now().timestamp()
            cx = int(width / 2 + (width / 4) * np.sin(t * 2))
            cv2.circle(frame, (cx, height // 2), 40, config["color"], -1)

            # 3. Texte (Label + Heure)
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            cv2.putText(frame, f"CAM: {config['label']}", (30, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
            cv2.putText(frame, f"TIME: {timestamp}", (30, 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 200, 200), 2)

            # 4. Envoi à FFmpeg
            process.stdin.write(frame.tobytes())

            # Petit sleep pour respecter le FPS (environ)
            time.sleep(1 / fps)

    except Exception as e:
        print(f"❌ Erreur sur {config['name']}: {e}")
    finally:
        process.stdin.close()
        process.wait()


# --- LANCEMENT ---
threads = []
for cam_config in CAMERAS:
    t = threading.Thread(target=start_camera_stream, args=(cam_config,))
    t.daemon = True  # S'arrête quand le script principal s'arrête
    t.start()
    threads.append(t)

print("\n📡 Tous les flux sont lancés. Appuyez sur Ctrl+C pour tout arrêter.\n")

try:
    while True:
        time.sleep(1)  # Garde le script principal en vie
except KeyboardInterrupt:
    print("\n🛑 Arrêt de tous les flux...")