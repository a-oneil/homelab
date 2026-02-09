"""UniFi Network plugin — client lookup, device management, bandwidth."""

import http.cookiejar
import json
import ssl
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, prompt_text, confirm, success, error, warn

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


def _api_post(opener, base, endpoint, data):
    """Make an authenticated POST request."""
    site = CFG.get("unifi_site", "default")
    url = f"{base}/api/s/{site}/{endpoint}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
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
            "UniFi Bandwidth": ("unifi_bandwidth", _bandwidth_stats),
            "UniFi Search": ("unifi_search", _search_client),
            "UniFi Block/Unblock": ("unifi_block", _block_unblock_client),
            "UniFi Restart Device": ("unifi_restart", _restart_device),
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
            "Bandwidth             — client traffic sorted by usage",
            "Search Client         — find device by name or MAC",
            "Block/Unblock Client  — restrict network access",
            "Restart Device        — reboot an AP, switch, or gateway",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 8:
            return
        elif idx == 6:
            continue
        elif idx == 7:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnifiPlugin())
        elif idx == 0:
            _show_clients()
        elif idx == 1:
            _show_devices()
        elif idx == 2:
            _bandwidth_stats()
        elif idx == 3:
            _search_client()
        elif idx == 4:
            _block_unblock_client()
        elif idx == 5:
            _restart_device()


def _format_uptime(seconds):
    if not seconds:
        return "?"
    d = int(seconds) // 86400
    h = (int(seconds) % 86400) // 3600
    if d > 0:
        return f"{d}d {h}h"
    m = (int(seconds) % 3600) // 60
    return f"{h}h {m}m"


def _format_bytes(b):
    """Format bytes as human-readable string."""
    if not b:
        return "0 B"
    b = int(b)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}" if b < 100 else f"{b:.0f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _show_clients():
    while True:
        opener, base = _get_session()
        if not opener:
            return

        data = _api_get(opener, base, "stat/sta")
        if not data or not data.get("data"):
            warn("No active clients found.")
            return

        clients = sorted(data["data"], key=lambda c: c.get("hostname", c.get("name", "zzz")))

        choices = []
        for c in clients:
            hostname = c.get("hostname", c.get("name", "?"))[:20]
            ip = c.get("ip", "?")
            mac = c.get("mac", "?")
            uptime = _format_uptime(c.get("uptime"))
            signal = f"{c.get('signal', '?')} dBm" if c.get("signal") else "wired"
            tx = c.get("tx_bytes", 0)
            rx = c.get("rx_bytes", 0)
            bw = ""
            if tx or rx:
                bw = f"  {C.DIM}↓{_format_bytes(rx)} ↑{_format_bytes(tx)}{C.RESET}"
            choices.append(f"{hostname:<22} {ip:<16} {mac:<18} {uptime:<10} {signal}{bw}")

        count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Clients ({count}):", choices)
        if idx <= count or idx == count + 1:
            continue
        return


def _show_devices():
    while True:
        opener, base = _get_session()
        if not opener:
            return

        data = _api_get(opener, base, "stat/device")
        if not data or not data.get("data"):
            warn("No network devices found.")
            return

        devices = data["data"]
        choices = []
        for d in devices:
            name = d.get("name", d.get("model", "?"))
            dtype = d.get("type", "?")
            ip = d.get("ip", "?")
            status = "adopted" if d.get("adopted") else "pending"
            uptime = _format_uptime(d.get("uptime"))
            choices.append(f"{name:<20} ({dtype})  IP: {ip}  {status}  Up: {uptime}")

        count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Network Devices ({count}):", choices)
        if idx <= count or idx == count + 1:
            continue
        return


def _bandwidth_stats():
    """Show clients sorted by bandwidth usage."""
    while True:
        opener, base = _get_session()
        if not opener:
            return

        data = _api_get(opener, base, "stat/sta")
        if not data or not data.get("data"):
            warn("No active clients found.")
            return

        clients = data["data"]
        clients.sort(key=lambda c: (c.get("tx_bytes", 0) + c.get("rx_bytes", 0)), reverse=True)

        choices = []
        for c in clients:
            hostname = c.get("hostname", c.get("name", "?"))[:20]
            tx = c.get("tx_bytes", 0)
            rx = c.get("rx_bytes", 0)
            total = tx + rx
            if total == 0:
                continue
            choices.append(
                f"{hostname:<22} {C.GREEN}↓{C.RESET}{_format_bytes(rx):>10}  "
                f"{C.ACCENT}↑{C.RESET}{_format_bytes(tx):>10}  "
                f"Total: {_format_bytes(total)}"
            )

        if not choices:
            warn("No bandwidth data available.")
            return

        count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Bandwidth ({count} clients):", choices)
        if idx <= count or idx == count + 1:
            continue
        return


def _block_unblock_client():
    """Block or unblock a client device."""
    opener, base = _get_session()
    if not opener:
        return

    data = _api_get(opener, base, "stat/sta")
    if not data or not data.get("data"):
        warn("No active clients found.")
        return

    clients = sorted(data["data"], key=lambda c: c.get("hostname", c.get("name", "zzz")))
    choices = []
    for c in clients:
        hostname = c.get("hostname", c.get("name", "?"))[:20]
        ip = c.get("ip", "?")
        mac = c.get("mac", "?")
        blocked = c.get("blocked", False)
        status = f"  {C.RED}BLOCKED{C.RESET}" if blocked else ""
        choices.append(f"{hostname:<22} {ip:<16} {mac:<18}{status}")

    choices.append("← Back")
    idx = pick_option("Select client to block/unblock:", choices)
    if idx >= len(clients):
        return

    client = clients[idx]
    mac = client.get("mac", "")
    hostname = client.get("hostname", client.get("name", mac))
    is_blocked = client.get("blocked", False)

    if is_blocked:
        action = "unblock"
        cmd = "unblock-sta"
    else:
        action = "block"
        cmd = "block-sta"

    if not confirm(f"{action.title()} '{hostname}' ({mac})?", default_yes=False):
        return

    result = _api_post(opener, base, "cmd/stamgr", {"cmd": cmd, "mac": mac})
    if result is not None:
        success(f"Client {action}ed: {hostname}")
    else:
        error(f"Failed to {action} client.")


def _restart_device():
    """Restart a network device (AP, switch, gateway)."""
    opener, base = _get_session()
    if not opener:
        return

    data = _api_get(opener, base, "stat/device")
    if not data or not data.get("data"):
        warn("No network devices found.")
        return

    devices = data["data"]
    choices = []
    for d in devices:
        name = d.get("name", d.get("model", "?"))
        dtype = d.get("type", "?")
        ip = d.get("ip", "?")
        uptime = _format_uptime(d.get("uptime"))
        choices.append(f"{name:<20} ({dtype})  IP: {ip}  Up: {uptime}")

    choices.append("← Back")
    idx = pick_option("Select device to restart:", choices)
    if idx >= len(devices):
        return

    device = devices[idx]
    mac = device.get("mac", "")
    name = device.get("name", device.get("model", mac))

    if not confirm(f"Restart '{name}' ({mac})? Device will be briefly offline.", default_yes=False):
        return

    result = _api_post(opener, base, "cmd/devmgr", {"cmd": "restart", "mac": mac})
    if result is not None:
        success(f"Restart initiated: {name}")
    else:
        error("Failed to restart device.")


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
