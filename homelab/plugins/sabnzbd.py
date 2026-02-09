"""SABnzbd download client plugin — add URLs and upload .nzb files."""

import os
import subprocess
import urllib.parse
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import pick_option, prompt_text, success, error


class SabnzbdPlugin(Plugin):
    name = "SABnzbd"
    key = "sabnzbd"

    def is_configured(self):
        return bool(CFG.get("sabnzbd_url") and CFG.get("sabnzbd_api_key"))

    def get_config_fields(self):
        return [
            ("sabnzbd_url", "SABnzbd URL", "e.g. http://192.168.1.100:8080", False),
            ("sabnzbd_api_key", "SABnzbd API Key", "", True),
        ]

    def get_menu_items(self):
        return [
            ("SABnzbd              — send NZBs and URLs", sabnzbd_menu),
        ]

    def get_actions(self):
        return {
            "SABnzbd Add URL": ("sabnzbd_add_url", _add_url),
            "SABnzbd Upload File": ("sabnzbd_upload", _upload_file),
        }


def sabnzbd_menu():
    while True:
        idx = pick_option("SABnzbd:", [
            "Add URL              — send a URL or NZB link",
            "Upload .nzb File     — upload from local machine",
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
            add_plugin_favorite(SabnzbdPlugin())
        elif idx == 0:
            _add_url()
        elif idx == 1:
            _upload_file()


def _add_url():
    """Send a URL to SABnzbd."""
    url = CFG.get("sabnzbd_url", "").rstrip("/")
    key = CFG.get("sabnzbd_api_key", "")
    if not url or not key:
        error("SABnzbd not configured. Set URL and API key in Settings.")
        return

    link = prompt_text("URL or NZB link:")
    if not link:
        return

    encoded = urllib.parse.quote(link, safe="")
    api_url = f"{url}/api?mode=addurl&name={encoded}&apikey={key}"
    try:
        req = urllib.request.Request(api_url)
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode()
        if "ok" in body.lower() or "nzo_id" in body.lower():
            success(f"Sent to SABnzbd: {link[:60]}")
            from homelab.notifications import notify
            notify("Homelab", "Download added to SABnzbd")
        else:
            error(f"SABnzbd response: {body.strip()}")
    except Exception as e:
        error(f"SABnzbd error: {e}")


def _upload_file():
    """Upload a local .nzb file to SABnzbd."""
    from homelab.files import browse_local

    url = CFG.get("sabnzbd_url", "").rstrip("/")
    key = CFG.get("sabnzbd_api_key", "")
    if not url or not key:
        error("SABnzbd not configured.")
        return

    filepath = browse_local()
    if not filepath:
        return

    api_url = f"{url}/api?mode=addlocalfile&apikey={key}"
    result = subprocess.run(
        ["curl", "-s", "-F", f"name=@{filepath}", api_url],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and "ok" in result.stdout.lower():
        success(f"Uploaded to SABnzbd: {os.path.basename(filepath)}")
    else:
        error(f"SABnzbd upload failed: {result.stdout.strip()}")
