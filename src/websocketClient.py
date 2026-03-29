import asyncio
import websockets
import json
import os
import subprocess


# --- CHARGEMENT DE LA CONFIGURATION ---
def load_config():
    config_path = "config.json"
    if not os.path.exists(config_path):
        print(f"❌ Erreur : {config_path} introuvable !")
        return None
    with open(config_path, "r") as f:
        return json.load(f)


config = load_config()
if not config:
    exit(1)

VPS_IP = config.get("server_url")
CAMERA_NAME = config.get("camera_name")


# --- FONCTION LED EMBARQUÉE ---
def set_onboard_led(state):
    """
    Contrôle la LED ACT (verte) de la Raspberry Pi.
    Nécessite souvent d'être lancé avec sudo ou d'avoir les droits sur /sys/class/leds/
    """
    # Chemin standard pour la LED verte sur RPi
    led_path = "/sys/class/leds/ACT/brightness"  # Parfois 'led0' selon le modèle

    if not os.path.exists(led_path):
        # Fallback pour d'autres modèles (RPi Zero/4/5 peuvent varier)
        led_path = "/sys/class/leds/led0/brightness"

    val = "1" if state == "ON" else "0"

    try:
        # On change le mode de la LED en 'none' pour pouvoir la piloter manuellement
        os.system(f"echo none | sudo tee /sys/class/leds/ACT/trigger > /dev/null")
        # On écrit la valeur
        os.system(f"echo {val} | sudo tee {led_path} > /dev/null")
    except Exception as e:
        print(f"❌ Erreur LED : {e}")


async def listen_commands():
    uri = f"ws://{VPS_IP}:8765"
    print(f"📡 Connexion au VPS ({VPS_IP}) pour la caméra : {CAMERA_NAME}")

    async for websocket in websockets.connect(uri):
        try:
            await websocket.send(json.dumps({
                "device_id": CAMERA_NAME,
                "action": "REGISTER"
            }))
            print(f"✅ Enregistré sur le serveur en tant que '{CAMERA_NAME}'")

            async for message in websocket:
                data = json.loads(message)
                cmd = data.get("command")
                payload = data.get("payload")

                print(f"📥 Ordre reçu : {cmd}")

                if cmd == "CMD_LED":
                    state = payload.get('state')  # "ON" ou "OFF"
                    print(f"💡 On-board LED -> {state}")
                    set_onboard_led(state)

                elif cmd == "CMD_MOVE":
                    direction = payload.get('direction')
                    print(f"🕹️ SERVO -> {direction}")

        except websockets.ConnectionClosed:
            print("⚠️ Connexion perdue avec le VPS, reconnexion...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(listen_commands())
    except KeyboardInterrupt:
        print("\n🛑 Arrêt du client.")