"""OPNsense router plugin — system status, interfaces, ARP, DHCP."""

import json
import ssl
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, confirm, info, success, error, warn, bar_chart

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to OPNsense (HTTP Basic)."""
    base = CFG.get("opnsense_url", "").rstrip("/")
    key = CFG.get("opnsense_api_key", "")
    secret = CFG.get("opnsense_api_secret", "")
    if not base or not key or not secret:
        return None

    url = f"{base}{endpoint}"

    import base64
    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()

    headers = {"Authorization": f"Basic {credentials}"}
    payload = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(data).encode()
    elif method == "POST":
        payload = b"{}"
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
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
            "Firmware Update       — check for and apply updates",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 7:
            return
        elif idx == 5:
            continue
        elif idx == 6:
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
        elif idx == 4:
            _firmware_update()


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


def _extract_addr(val):
    """Pull a displayable IP string from addr4/ipv4 which may be list or str."""
    if not val:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts = []
        for a in val:
            if isinstance(a, dict):
                parts.append(a.get("address", a.get("ipaddr", str(a))))
            elif isinstance(a, str):
                parts.append(a)
        return ", ".join(parts)
    return str(val)


def _interfaces():
    data = _api("/api/interfaces/overview/export")
    if not data:
        error("Could not fetch interface info.")
        return

    # Normalize: if it's a list, convert to dict keyed by index
    items = data.items() if isinstance(data, dict) else enumerate(data)

    print(f"\n  {C.BOLD}OPNsense Interfaces{C.RESET}\n")

    for iface_key, d in items:
        if not isinstance(d, dict):
            continue

        desc = d.get("description", str(iface_key))
        status = d.get("status", "?")
        addr = _extract_addr(d.get("addr4")) or _extract_addr(d.get("ipv4"))
        enabled = d.get("enabled", True)

        # Skip unassigned interfaces that are down with no IP
        if desc == "Unassigned Interface" and status != "up" and not addr:
            continue

        icon = f"{C.GREEN}●{C.RESET}" if status == "up" else f"{C.DIM}○{C.RESET}"
        line = f"  {icon} {desc:<20}"

        device = d.get("device", "")
        if device:
            line += f" {C.DIM}{device:<10}{C.RESET}"

        vlan = d.get("vlan_tag", "")
        if vlan:
            line += f" {C.ACCENT}VLAN {vlan}{C.RESET}"

        if addr:
            line += f"  {addr}"

        # Gateway
        gateways = d.get("gateways", [])
        if isinstance(gateways, list):
            for gw in gateways:
                if isinstance(gw, dict):
                    gw_addr = gw.get("gateway", gw.get("address", ""))
                    gw_name = gw.get("name", "")
                    if gw_addr:
                        label = f"{gw_name} {gw_addr}" if gw_name else gw_addr
                        line += f"  {C.DIM}gw {label}{C.RESET}"

        # Traffic stats
        stats = d.get("statistics", {})
        if isinstance(stats, dict):
            rx = stats.get("bytes received", stats.get("bytes_received", 0))
            tx = stats.get("bytes transmitted", stats.get("bytes_transmitted", 0))
            try:
                rx_mb = int(rx) / (1024 ** 2)
                tx_mb = int(tx) / (1024 ** 2)
                if rx_mb > 0 or tx_mb > 0:
                    line += f"  {C.DIM}↓{rx_mb:.0f}MB ↑{tx_mb:.0f}MB{C.RESET}"
            except (ValueError, TypeError):
                pass

        if not enabled:
            line += f"  {C.YELLOW}(disabled){C.RESET}"

        print(line)

    # IPv6 summary
    all_ifaces = data.values() if isinstance(data, dict) else data
    v6_count = sum(1 for d in all_ifaces if isinstance(d, dict) and _extract_addr(d.get("addr6")))
    if v6_count:
        print(f"\n  {C.DIM}{v6_count} interface(s) with IPv6{C.RESET}")

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


def _firmware_update():
    """Check for and apply OPNsense firmware updates."""
    info("Checking for updates...")
    _api("/api/core/firmware/check", method="POST")

    # Poll status until the check completes
    import time as _time
    for _ in range(15):
        status = _api("/api/core/firmware/status", method="POST")
        if status and status.get("status") != "running":
            break
        _time.sleep(2)

    if not status:
        error("Could not check firmware status.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}OPNsense Firmware{C.RESET}\n")

    current = status.get("product_version", status.get("product", {}).get("product_version", "?"))
    print(f"  {C.BOLD}Current:{C.RESET}  {current}")

    # Show available updates
    new_packages = status.get("new_packages", [])
    upgrade_packages = status.get("upgrade_packages", [])
    reinstall_packages = status.get("reinstall_packages", [])
    downgrade_packages = status.get("downgrade_packages", [])

    needs_update = status.get("status_upgrade_action", "") == "all"
    update_msg = status.get("status_msg", "")

    if update_msg:
        print(f"  {C.BOLD}Status:{C.RESET}   {update_msg}")

    if upgrade_packages:
        print(f"  {C.ACCENT}{len(upgrade_packages)} package(s) to upgrade{C.RESET}")
    if new_packages:
        print(f"  {C.ACCENT}{len(new_packages)} new package(s){C.RESET}")
    if reinstall_packages:
        print(f"  {C.DIM}{len(reinstall_packages)} to reinstall{C.RESET}")
    if downgrade_packages:
        print(f"  {C.YELLOW}{len(downgrade_packages)} to downgrade{C.RESET}")

    if not needs_update and not upgrade_packages and not new_packages:
        success("System is up to date.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Show changelog if available
    changelog = status.get("product", {}).get("product_latest", "")
    if changelog and changelog != current:
        print(f"  {C.BOLD}Latest:{C.RESET}   {changelog}")

    print()
    if not confirm("Apply firmware update?", default_yes=False):
        return

    info("Starting firmware update...")
    result = _api("/api/core/firmware/update", method="POST")
    if result and result.get("status", "") == "ok":
        success("Firmware update started. OPNsense will reboot when complete.")
    else:
        error("Failed to start firmware update.")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
