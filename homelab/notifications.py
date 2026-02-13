"""Platform-aware desktop notifications and Discord webhook."""

import json
import subprocess
import sys
import threading
import urllib.request

from homelab.config import CFG


def _notify_discord(title, message):
    """Post a notification to Discord webhook (fire-and-forget)."""
    webhook_url = CFG.get("discord_webhook_url", "")
    if not webhook_url:
        return

    def _send():
        try:
            payload = json.dumps(
                {"content": f"**{title}**: {message}"}
            ).encode()
            req = urllib.request.Request(
                webhook_url, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Homelab"},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"\n⚠ Discord notification failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


def notify(title, message):
    """Send a desktop notification and Discord webhook."""
    if not CFG.get("notifications", True):
        return
    notify_desktop(title, message)
    _notify_discord(title, message)


def notify_desktop(title, message):
    """Platform-aware desktop notification."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"'],
                capture_output=True, timeout=5,
            )
        elif sys.platform == "linux":
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True, timeout=5,
            )
    except Exception:
        pass


def copy_to_clipboard(text):
    """Platform-aware clipboard copy."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=5)
        elif sys.platform == "linux":
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True, timeout=5)
        elif sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode(), check=True, timeout=5)
        else:
            return False
        return True
    except Exception:
        return False
