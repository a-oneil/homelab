"""OPNsense router plugin — system status, interfaces, ARP, DHCP."""

import json
import ssl
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, error, warn, bar_chart

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
            CFG.get("opnsense_url") and CFG.get("opnsense_api_key") and CFG.get("opnsense_api_secret")
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
    time_data = _api("/api/diagnostics/system/system_time")
    if time_data and time_data.get("uptime"):
        uptime = time_data["uptime"]
        load = time_data.get("loadavg", "")
        parts = [f"up {uptime}"]
        if load and load != "N/A":
            parts.append(f"load {load}")
        _HEADER_CACHE["stats"] = f"OPNsense: {', '.join(parts)}"
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
    sys_info = _api("/api/diagnostics/system/system_information")
    time_data = _api("/api/diagnostics/system/system_time")
    resources = _api("/api/diagnostics/system/system_resources")
    cpu_data = _api("/api/diagnostics/cpu_usage/get_c_p_u_type")
    firmware = _api("/api/core/firmware/info")
    temps = _api("/api/diagnostics/system/system_temperature")
    disk_data = _api("/api/diagnostics/system/system_disk")

    if not sys_info and not time_data and not resources:
        error("Could not fetch system status.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}OPNsense System Status{C.RESET}\n")

    # Hostname + version
    if sys_info:
        if sys_info.get("name"):
            print(f"  {C.BOLD}Host:{C.RESET}     {sys_info['name']}")
        versions = sys_info.get("versions", [])
        if versions:
            print(f"  {C.BOLD}Version:{C.RESET}  {versions[0]}")

    # Firmware
    if firmware and firmware.get("product_version"):
        name = firmware.get("product_name", "OPNsense")
        print(f"  {C.BOLD}Firmware:{C.RESET} {name} {firmware['product_version']}")

    # Uptime + load
    if time_data:
        if time_data.get("uptime"):
            print(f"  {C.BOLD}Uptime:{C.RESET}   {time_data['uptime']}")
        if time_data.get("loadavg") and time_data["loadavg"] != "N/A":
            print(f"  {C.BOLD}Load:{C.RESET}     {time_data['loadavg']}")

    # CPU
    if cpu_data and isinstance(cpu_data, list) and cpu_data:
        print(f"  {C.BOLD}CPU:{C.RESET}      {cpu_data[0]}")

    # Memory
    if resources and isinstance(resources, dict):
        mem = resources.get("memory", {})
        used_mb = mem.get("used_frmt")
        total_mb = mem.get("total_frmt")
        if used_mb and total_mb:
            try:
                used = int(used_mb)
                total = int(total_mb)
                chart = bar_chart(used, total)
                print(f"  {C.BOLD}RAM:{C.RESET}      {used} / {total} MB  {chart}")
            except (ValueError, TypeError):
                print(f"  {C.BOLD}RAM:{C.RESET}      {used_mb} / {total_mb} MB")
        arc_txt = mem.get("arc_txt")
        if arc_txt:
            print(f"  {C.BOLD}ARC:{C.RESET}      {arc_txt}")

    # Temperature
    if temps and isinstance(temps, list):
        cpu_temps = [t for t in temps if t.get("type") == "cpu"]
        zone_temps = [t for t in temps if t.get("type") == "zone"]
        for t in (cpu_temps or zone_temps)[:2]:
            label = t.get("type_translated", "Temp")
            print(f"  {C.BOLD}{label}:{C.RESET}    {t.get('temperature', '?')}")

    # Disk
    if disk_data and isinstance(disk_data, dict):
        for dev in disk_data.get("devices", []):
            mp = dev.get("mountpoint", "?")
            used_pct = dev.get("used_pct", "?")
            size = dev.get("blocks", "?")
            print(f"  {C.BOLD}Disk {mp}:{C.RESET}  {used_pct} of {size}")

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

    rows = []
    for entry in entries:
        if isinstance(entry, dict):
            ip = entry.get("ip", "?")
            mac = entry.get("mac", "?")
            iface = entry.get("intf", entry.get("interface", "?"))
            rows.append(f"{ip:<18} {mac:<20} {iface}")

    scrollable_list(f"ARP Table ({len(rows)} entries):", rows)


def _dhcp_leases():
    data = _api("/api/dhcpv4/leases/searchLease")
    if not data:
        error("Could not fetch DHCP leases.")
        return

    rows = data.get("rows", [])
    if not rows:
        warn("No DHCP leases found.")
        return

    lines = []
    for lease in rows:
        ip = lease.get("address", "?")
        mac = lease.get("mac", "?")
        hostname = lease.get("hostname", "?")
        lines.append(f"{ip:<18} {mac:<20} {hostname}")

    scrollable_list(f"DHCP Leases ({len(lines)}):", lines)
