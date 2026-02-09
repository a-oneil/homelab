"""UniFi Network plugin — client lookup and device management."""

import http.cookiejar
import json
import ssl
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import pick_option, scrollable_list, prompt_text, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _get_session():
    """Authenticate and return an opener with session cookie."""
    base = CFG.get("unifi_url", "").rstrip("/")
    username = CFG.get("unifi_username", "")
    password = CFG.get("unifi_password", "")
    if not base or not username:
        return None, None

    cj = http.cookiejar.CookieJar()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx),
    )

    login_data = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{base}/api/login",
        data=login_data,
        headers={"Content-Type": "application/json"},
    )
    try:
        opener.open(req, timeout=10)
        return opener, base
    except Exception as e:
        error(f"UniFi login failed: {e}")
        return None, None


def _api_get(opener, base, endpoint):
    """Make an authenticated GET request."""
    site = CFG.get("unifi_site", "default")
    url = f"{base}/api/s/{site}/{endpoint}"
    req = urllib.request.Request(url)
    try:
        with opener.open(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"UniFi API error: {e}")
        return None


class UnifiPlugin(Plugin):
    name = "UniFi"
    key = "unifi"

    def is_configured(self):
        return bool(CFG.get("unifi_url") and CFG.get("unifi_username") and CFG.get("unifi_password"))

    def get_config_fields(self):
        return [
            ("unifi_url", "UniFi URL", "e.g. https://192.168.1.1:8443", False),
            ("unifi_username", "UniFi Username", "", False),
            ("unifi_password", "UniFi Password", "", True),
            ("unifi_site", "UniFi Site", "default", False),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_menu_items(self):
        return [
            ("UniFi                — network clients and devices", unifi_menu),
        ]

    def get_actions(self):
        return {
            "UniFi Clients": ("unifi_clients", _show_clients),
            "UniFi Devices": ("unifi_devices", _show_devices),
            "UniFi Search": ("unifi_search", _search_client),
        }


def _fetch_stats():
    opener, base = _get_session()
    if not opener:
        return
    data = _api_get(opener, base, "stat/sta")
    if data and data.get("data"):
        count = len(data["data"])
        _HEADER_CACHE["stats"] = f"UniFi: {count} clients"
    _HEADER_CACHE["timestamp"] = time.time()


def unifi_menu():
    while True:
        idx = pick_option("UniFi:", [
            "Active Clients        — connected devices with IP, uptime, signal",
            "Network Devices       — APs, switches, gateways",
            "Search Client         — find device by name or MAC",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 5:
            return
        elif idx == 3:
            continue
        elif idx == 4:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnifiPlugin())
        elif idx == 0:
            _show_clients()
        elif idx == 1:
            _show_devices()
        elif idx == 2:
            _search_client()


def _format_uptime(seconds):
    if not seconds:
        return "?"
    d = int(seconds) // 86400
    h = (int(seconds) % 86400) // 3600
    if d > 0:
        return f"{d}d {h}h"
    m = (int(seconds) % 3600) // 60
    return f"{h}h {m}m"


def _show_clients():
    opener, base = _get_session()
    if not opener:
        return

    data = _api_get(opener, base, "stat/sta")
    if not data or not data.get("data"):
        warn("No active clients found.")
        return

    clients = sorted(data["data"], key=lambda c: c.get("hostname", c.get("name", "zzz")))

    rows = []
    for c in clients:
        hostname = c.get("hostname", c.get("name", "?"))[:20]
        ip = c.get("ip", "?")
        mac = c.get("mac", "?")
        uptime = _format_uptime(c.get("uptime"))
        signal = f"{c.get('signal', '?')} dBm" if c.get("signal") else "wired"
        rows.append(f"{hostname:<22} {ip:<16} {mac:<18} {uptime:<10} {signal}")

    scrollable_list(f"Clients ({len(rows)}):", rows)


def _show_devices():
    opener, base = _get_session()
    if not opener:
        return

    data = _api_get(opener, base, "stat/device")
    if not data or not data.get("data"):
        warn("No network devices found.")
        return

    devices = data["data"]
    rows = []
    for d in devices:
        name = d.get("name", d.get("model", "?"))
        dtype = d.get("type", "?")
        ip = d.get("ip", "?")
        status = "adopted" if d.get("adopted") else "pending"
        uptime = _format_uptime(d.get("uptime"))
        rows.append(f"{name:<20} ({dtype})  IP: {ip}  {status}  Up: {uptime}")

    scrollable_list(f"Network Devices ({len(rows)}):", rows)


def _search_client():
    query = prompt_text("Search by hostname or MAC:")
    if not query:
        return

    opener, base = _get_session()
    if not opener:
        return

    data = _api_get(opener, base, "stat/sta")
    if not data or not data.get("data"):
        warn("No clients found.")
        return

    query_lower = query.lower()
    matches = [
        c for c in data["data"]
        if query_lower in c.get("hostname", "").lower()
        or query_lower in c.get("name", "").lower()
        or query_lower in c.get("mac", "").lower()
    ]

    if not matches:
        warn("No matching clients found.")
        return

    rows = []
    for c in matches:
        hostname = c.get("hostname", c.get("name", "?"))
        ip = c.get("ip", "?")
        mac = c.get("mac", "?")
        rows.append(f"{hostname:<22} IP: {ip:<16} MAC: {mac}")

    scrollable_list(f"Search results ({len(rows)}):", rows)
