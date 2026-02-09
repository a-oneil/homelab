"""OPNsense router plugin — system status, interfaces, ARP, DHCP."""

import json
import ssl
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint):
    """Make an authenticated API call to OPNsense (HTTP Basic)."""
    base = CFG.get("opnsense_url", "").rstrip("/")
    key = CFG.get("opnsense_api_key", "")
    secret = CFG.get("opnsense_api_secret", "")
    if not base or not key or not secret:
        return None

    url = f"{base}{endpoint}"

    # HTTP Basic auth
    import base64
    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()

    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {credentials}",
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"OPNsense API error: {e}")
        return None


class OpnsensePlugin(Plugin):
    name = "OPNsense"
    key = "opnsense"

    def is_configured(self):
        return bool(
            CFG.get("opnsense_url")
            and CFG.get("opnsense_api_key")
            and CFG.get("opnsense_api_secret")
        )

    def get_config_fields(self):
        return [
            ("opnsense_url", "OPNsense URL", "e.g. https://192.168.1.1", False),
            ("opnsense_api_key", "OPNsense API Key", "", True),
            ("opnsense_api_secret", "OPNsense API Secret", "", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_menu_items(self):
        return [
            ("OPNsense             — router status and interfaces", opnsense_menu),
        ]

    def get_actions(self):
        return {
            "OPNsense Status": ("opnsense_status", _system_status),
            "OPNsense Interfaces": ("opnsense_interfaces", _interfaces),
            "OPNsense ARP Table": ("opnsense_arp", _arp_table),
            "OPNsense DHCP Leases": ("opnsense_dhcp", _dhcp_leases),
        }


def _fetch_stats():
    data = _api("/api/core/system/status")
    if data:
        _HEADER_CACHE["stats"] = "OPNsense: WAN up"
    _HEADER_CACHE["timestamp"] = time.time()


def opnsense_menu():
    while True:
        idx = pick_option("OPNsense:", [
            "System Status         — uptime, firmware, CPU, RAM",
            "Interfaces            — WAN/LAN status, traffic stats",
            "ARP Table             — IP <-> MAC mappings",
            "DHCP Leases           — active leases",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 6:
            return
        elif idx == 4:
            continue
        elif idx == 5:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(OpnsensePlugin())
        elif idx == 0:
            _system_status()
        elif idx == 1:
            _interfaces()
        elif idx == 2:
            _arp_table()
        elif idx == 3:
            _dhcp_leases()


def _system_status():
    data = _api("/api/core/system/status")
    if not data:
        error("Could not fetch system status.")
        return

    print(f"\n  {C.BOLD}OPNsense System Status{C.RESET}\n")

    for key, label in [
        ("uptime", "Uptime"),
        ("firmware", "Firmware"),
        ("cpu_type", "CPU"),
        ("cpu_count", "CPU Cores"),
    ]:
        val = data.get(key, "?")
        if val and val != "?":
            print(f"  {C.BOLD}{label}:{C.RESET}  {val}")

    # Memory info
    mem_total = data.get("physmem")
    mem_used = data.get("physmem_used")
    if mem_total and mem_used:
        try:
            total_gb = int(mem_total) / (1024**3)
            used_gb = int(mem_used) / (1024**3)
            print(f"  {C.BOLD}RAM:{C.RESET}      {used_gb:.1f} / {total_gb:.1f} GB")
        except (ValueError, TypeError):
            pass

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _interfaces():
    data = _api("/api/interfaces/overview/export")
    if not data:
        # Try alternative endpoint
        data = _api("/api/diagnostics/interface/getInterfaceStatistics")
    if not data:
        error("Could not fetch interface info.")
        return

    print(f"\n  {C.BOLD}OPNsense Interfaces{C.RESET}\n")

    if isinstance(data, dict):
        for iface_name, iface_data in data.items():
            if isinstance(iface_data, dict):
                status = iface_data.get("status", "?")
                addr = iface_data.get("ipaddr", iface_data.get("addr", "?"))
                print(f"  {C.ACCENT}{iface_name}{C.RESET}  Status: {status}  IP: {addr}")
    elif isinstance(data, list):
        for iface in data:
            name = iface.get("description", iface.get("name", "?"))
            status = iface.get("status", "?")
            addr = iface.get("addr", "?")
            print(f"  {C.ACCENT}{name}{C.RESET}  Status: {status}  IP: {addr}")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _arp_table():
    data = _api("/api/diagnostics/interface/getArp")
    if not data:
        error("Could not fetch ARP table.")
        return

    entries = data if isinstance(data, list) else data.get("rows", data.get("arp", []))
    if not entries:
        warn("ARP table is empty.")
        return

    print(f"\n  {C.BOLD}ARP Table{C.RESET}\n")
    print(f"  {C.BOLD}{'IP':<18} {'MAC':<20} {'Interface'}{C.RESET}")
    print(f"  {C.DIM}{'─' * 55}{C.RESET}")

    for entry in entries[:50]:
        if isinstance(entry, dict):
            ip = entry.get("ip", "?")
            mac = entry.get("mac", "?")
            iface = entry.get("intf", entry.get("interface", "?"))
            print(f"  {ip:<18} {mac:<20} {iface}")

    if len(entries) > 50:
        print(f"\n  {C.DIM}Showing 50 of {len(entries)} entries{C.RESET}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _dhcp_leases():
    data = _api("/api/dhcpv4/leases/searchLease")
    if not data:
        error("Could not fetch DHCP leases.")
        return

    rows = data.get("rows", [])
    if not rows:
        warn("No DHCP leases found.")
        return

    print(f"\n  {C.BOLD}DHCP Leases{C.RESET}\n")
    print(f"  {C.BOLD}{'IP':<18} {'MAC':<20} {'Hostname'}{C.RESET}")
    print(f"  {C.DIM}{'─' * 60}{C.RESET}")

    for lease in rows[:50]:
        ip = lease.get("address", "?")
        mac = lease.get("mac", "?")
        hostname = lease.get("hostname", "?")
        print(f"  {ip:<18} {mac:<20} {hostname}")

    if len(rows) > 50:
        print(f"\n  {C.DIM}Showing 50 of {len(rows)} leases{C.RESET}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
