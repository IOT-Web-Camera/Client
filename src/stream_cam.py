import subprocess
import json
import os
import sys


def run_stream():
    config_file = 'config.json'

    if not os.path.exists(config_file):
        print(f"[-] Erreur : Le fichier '{config_file}' est introuvable.")
        sys.exit(1)

    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"[-] Erreur de lecture du JSON : {e}")
        sys.exit(1)

    vps_ip = config.get("server_url")
    stream_name = config.get("camera_name")
    stream_pass = config.get("stream_pass")

    # On définit l'utilisateur localement.
    # Ton ApiController actuel semble attendre 'admin' ou ne pas le vérifier strictement
    stream_user = "admin"

    if not all([vps_ip, stream_name, stream_pass]):
        print("[-] Erreur : Données manquantes dans le config.json.")
        sys.exit(1)

    # Commande FFmpeg : On utilise l'authentification dans l'URL RTSP
    # Format : rtsp://user:password@ip:port/path
    pipeline = (
        f"rpicam-vid -t 0 --inline --nopreview --width 1280 --height 720 --framerate 20 "
        f"--codec h264 -o - | "
        f"ffmpeg -re -i - -c copy -f rtsp -rtsp_transport tcp "
        f"rtsp://{stream_user}:{stream_pass}@{vps_ip}:8554/{stream_name}"
    )

    print(f"[*] Tentative de connexion à {vps_ip}...")
    print(f"[*] Flux : {stream_name}")

    try:
        subprocess.run(pipeline, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[-] Erreur de stream (vérifie tes identifiants en DB) : {e}")
    except KeyboardInterrupt:
        print("\n[!] Arrêt manuel.")


if __name__ == "__main__":
    run_stream()