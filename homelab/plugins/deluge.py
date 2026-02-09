"""Deluge download client plugin — add torrents and magnet links."""

import base64
import json
import os
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import pick_option, prompt_text, success, error


class DelugePlugin(Plugin):
    name = "Deluge"
    key = "deluge"

    def is_configured(self):
        return bool(CFG.get("deluge_url"))

    def get_config_fields(self):
        return [
            ("deluge_url", "Deluge URL", "e.g. http://192.168.1.100:8112", False),
            ("deluge_password", "Deluge Password", "Web UI password", True),
        ]

    def get_menu_items(self):
        return [
            ("Deluge               — send torrents and magnet links", deluge_menu),
        ]

    def get_actions(self):
        return {
            "Deluge Add URL": ("deluge_add_url", _add_url),
            "Deluge Upload File": ("deluge_upload", _upload_file),
        }


def _rpc(method, params=None):
    """Make a JSON-RPC call to Deluge."""
    url = CFG.get("deluge_url", "").rstrip("/")
    if not url:
        error("Deluge not configured.")
        return None
    payload = json.dumps({
        "method": method,
        "params": params or [],
        "id": 1,
    }).encode()
    req = urllib.request.Request(
        f"{url}/json",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        error(f"Deluge error: {e}")
        return None


def deluge_menu():
    while True:
        idx = pick_option("Deluge:", [
            "Add URL / Magnet     — send a URL or magnet link",
            "Upload .torrent File — upload from local machine",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 2:
            continue
        elif idx == 3:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(DelugePlugin())
        elif idx == 0:
            _add_url()
        elif idx == 1:
            _upload_file()


def _add_url():
    """Send a URL or magnet link to Deluge."""
    url = CFG.get("deluge_url", "")
    if not url:
        error("Deluge not configured. Set URL in Settings.")
        return

    link = prompt_text("URL or magnet link:")
    if not link:
        return

    pw = CFG.get("deluge_password", "")
    auth = _rpc("auth.login", [pw])
    if not auth or not auth.get("result"):
        error("Deluge authentication failed.")
        return

    if link.startswith("magnet:"):
        result = _rpc("core.add_torrent_magnet", [link, {}])
    else:
        result = _rpc("core.add_torrent_url", [link, {}])

    if result and result.get("result"):
        success(f"Sent to Deluge: {link[:60]}")
        from homelab.notifications import notify
        notify("Homelab", "Download added to Deluge")
    else:
        err = result.get("error", {}).get("message", "Unknown error") if result else "No response"
        error(f"Deluge error: {err}")


def _upload_file():
    """Upload a local .torrent file to Deluge."""
    from homelab.files import browse_local

    url = CFG.get("deluge_url", "")
    if not url:
        error("Deluge not configured.")
        return

    filepath = browse_local()
    if not filepath:
        return

    pw = CFG.get("deluge_password", "")
    auth = _rpc("auth.login", [pw])
    if not auth or not auth.get("result"):
        error("Deluge authentication failed.")
        return

    with open(filepath, "rb") as f:
        b64data = base64.b64encode(f.read()).decode()
    fname = os.path.basename(filepath)
    result = _rpc("core.add_torrent_file", [fname, b64data, {}])
    if result and result.get("result"):
        success(f"Uploaded to Deluge: {fname}")
    else:
        error("Deluge upload failed.")
