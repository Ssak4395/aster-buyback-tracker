# app.py
import os
import threading
from flask import Flask, jsonify

# Import your poller main() function. Make sure poll_latest_tx_and_balance defines main().
try:
    from poll_latest_tx_and_balance import main as poll_main
except Exception as e:
    print("Could not import poll_main:", e)
    poll_main = None

app = Flask(__name__)
_bg_thread = None

def start_background_poller():
    global _bg_thread
    if poll_main is None:
        print("poll_main not available; skipping poller start.")
        return
    if _bg_thread and _bg_thread.is_alive():
        return
    def runner():
        try:
            poll_main()
        except Exception as e:
            print("poll_main crashed:", e)
    _bg_thread = threading.Thread(target=runner, daemon=True, name="poller-thread")
    _bg_thread.start()

@app.route("/")
def index():
    return "ASTER tracker running. Use /health for status.\n", 200, {"Content-Type": "text/plain"}

@app.route("/health")
def health():
    running = (_bg_thread is not None and _bg_thread.is_alive())
    return jsonify({"status": "ok", "poller_running": running}), 200

if __name__ == "__main__":
    start_background_poller()
    port = int(os.environ.get("PORT", "5000"))
    # Bind 0.0.0.0 so Render can route to it
    app.run(host="0.0.0.0", port=port)