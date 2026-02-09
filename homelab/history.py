"""Transfer history loading, saving, and logging."""

import datetime
import json
import os

from homelab.config import HISTORY_PATH


def load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, "r") as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)


def log_transfer(direction, method, url, destination, item_count):
    history = load_history()
    history.append({
        "timestamp": datetime.datetime.now().isoformat(),
        "direction": direction,
        "method": method,
        "url": url or "",
        "destination": destination,
        "item_count": item_count,
    })
    save_history(history[-100:])
