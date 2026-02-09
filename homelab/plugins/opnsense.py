"""OPNsense router plugin — system status, interfaces, firewall, services, VPN."""

import json
import ssl
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, confirm, info, success, error, warn, bar_chart

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None, silent=False):
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
        if not silent:
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
            "OPNsense Firewall Rules": ("opnsense_fw_rules", _firewall_rules),
            "OPNsense Firewall Log": ("opnsense_fw_log", _firewall_log),
            "OPNsense ARP Table": ("opnsense_arp", _arp_table),
            "OPNsense DHCP Leases": ("opnsense_dhcp", _dhcp_leases),
            "OPNsense Services": ("opnsense_services", _services),
            "OPNsense VPN Status": ("opnsense_vpn", _vpn_status),
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
            "Firewall Rules        — filter rules with action and status",
            "Firewall Log          — recent firewall log entries",
            "ARP Table             — IP <-> MAC mappings",
            "DHCP Leases           — active leases",
            "Services              — view and restart system services",
            "VPN Status            — WireGuard and OpenVPN connections",
            "Firmware Update       — check for and apply updates",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 11:
            return
        elif idx == 9:
            continue
        elif idx == 10:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(OpnsensePlugin())
        elif idx == 0:
            _system_status()
        elif idx == 1:
            _interfaces()
        elif idx == 2:
            _firewall_rules()
        elif idx == 3:
            _firewall_log()
        elif idx == 4:
            _arp_table()
        elif idx == 5:
            _dhcp_leases()
        elif idx == 6:
            _services()
        elif idx == 7:
            _vpn_status()
        elif idx == 8:
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


def _format_count(n):
    """Format a large number with K/M suffixes."""
    try:
        n = int(n)
    except (ValueError, TypeError):
        return str(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _format_rule_bytes(b):
    """Format bytes for rule stats."""
    try:
        b = int(b)
    except (ValueError, TypeError):
        return str(b)
    if b == 0:
        return "0"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"


def _fetch_rule_descriptions():
    """Fetch rule descriptions from searchRule and build hash-to-label map."""
    desc_map = {}
    data = _api(
        "/api/firewall/filter/searchRule?show_all=1", method="POST",
        data={"current": 1, "rowCount": 500, "sort": {}, "searchPhrase": ""},
        silent=True
    )
    if not data or not isinstance(data, dict):
        return desc_map
    for rule in data.get("rows", []):
        uuid = rule.get("uuid", "")
        desc = rule.get("description", "")
        if not desc:
            action = rule.get("action", "")
            interface = rule.get("interface", "")
            source = rule.get("source_net", "")
            dest = rule.get("destination_net", "")
            parts = [p for p in [action, interface] if p]
            if source:
                parts.append(source)
            if dest:
                parts.append(f"-> {dest}")
            desc = " ".join(parts)
        if uuid and desc:
            desc_map[uuid] = desc
    return desc_map


def _firewall_rules():
    """Display all pf firewall rules with descriptions and hit statistics."""
    while True:
        data = _api("/api/firewall/filter_util/ruleStats")
        if not data or not isinstance(data, dict) or data.get("status") != "ok":
            error("Could not fetch firewall rules.")
            return

        stats = data.get("stats", {})
        if not stats:
            warn("No firewall rules found.")
            return

        desc_map = _fetch_rule_descriptions()

        choices = []
        for i, (rule_hash, rule) in enumerate(stats.items(), 1):
            packets = rule.get("packets", 0)
            rule_bytes = rule.get("bytes", 0)
            states = rule.get("states", 0)
            pf_count = rule.get("pf_rules", 0)

            desc = desc_map.get(rule_hash, "")
            if desc:
                label = desc[:30]
            else:
                label = f"#{i} {rule_hash[:8]}"

            active = int(packets) > 0 if packets else False
            icon = f"{C.GREEN}●{C.RESET}" if active else f"{C.DIM}○{C.RESET}"

            parts = [
                f"{icon} {label:<30}",
                f"pkts: {_format_count(packets):>7}",
                f"bytes: {_format_rule_bytes(rule_bytes):>7}",
                f"states: {_format_count(states):>5}",
            ]
            if pf_count > 1:
                parts.append(f"{C.DIM}({pf_count} pf rules){C.RESET}")

            choices.append("  ".join(parts))

        rule_count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Firewall Rules ({rule_count}):", choices)
        if idx == rule_count:
            continue
        elif idx == rule_count + 1:
            continue
        elif idx == rule_count + 2:
            return


def _parse_filterlog(line):
    """Parse a BSD filterlog CSV line into a dict.

    Format: rulenr,subnr,anchor,ruleidentifier,interface,reason,action,dir,ipver,...
    IPv4 continues: tos,ecn,ttl,id,offset,flags,protonum,protoname,length,src,dst,...
    TCP/UDP adds: srcport,dstport,...
    """
    fields = line.split(",")
    if len(fields) < 10:
        return None
    result = {
        "interface": fields[4] if len(fields) > 4 else "?",
        "action": fields[6] if len(fields) > 6 else "?",
        "direction": fields[7] if len(fields) > 7 else "?",
        "ipver": fields[8] if len(fields) > 8 else "?",
    }
    if result["ipver"] == "4" and len(fields) > 19:
        result["proto"] = fields[16]
        result["src"] = fields[18]
        result["dst"] = fields[19]
        if len(fields) > 21:
            result["src"] += f":{fields[20]}"
            result["dst"] += f":{fields[21]}"
    elif result["ipver"] == "6" and len(fields) > 18:
        result["proto"] = fields[12]
        result["src"] = fields[15]
        result["dst"] = fields[16]
        if len(fields) > 18:
            result["src"] += f":{fields[17]}"
            result["dst"] += f":{fields[18]}"
    return result


def _firewall_log():
    """View recent firewall log entries."""
    while True:
        data = _api("/api/diagnostics/log/core/filter", method="POST",
                    data={"current": 1, "rowCount": 50, "sort": {}, "searchPhrase": ""})
        if not data:
            error("Could not fetch firewall logs.")
            return

        entries = data if isinstance(data, list) else data.get("rows", [])
        if not entries:
            warn("No log entries found.")
            return

        choices = []
        for entry in entries:
            timestamp = entry.get("timestamp", "?")
            if "T" in str(timestamp):
                timestamp = timestamp.split("T")[1]

            line = entry.get("line", "").strip()
            parsed = _parse_filterlog(line)

            if parsed:
                action = parsed.get("action", "?")
                interface = parsed.get("interface", "?")
                src = parsed.get("src", "?")
                dst = parsed.get("dst", "?")
                proto = parsed.get("proto", "?")

                if action.lower() == "block":
                    action_str = f"{C.RED}{action:<6}{C.RESET}"
                elif action.lower() == "pass":
                    action_str = f"{C.GREEN}{action:<6}{C.RESET}"
                else:
                    action_str = f"{action:<6}"

                choices.append(f"{timestamp:<9} {action_str} {interface:<6} {proto:<6} {src:<22} -> {dst}")
            else:
                msg = line[:70] if line else entry.get("process_name", "?")
                choices.append(f"{timestamp:<9} {C.DIM}{msg}{C.RESET}")

        entry_count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Firewall Log ({entry_count} entries):", choices)
        if idx == entry_count:
            continue
        elif idx == entry_count + 1:
            continue
        elif idx == entry_count + 2:
            return


def _services():
    """View and restart OPNsense services."""
    while True:
        data = _api("/api/core/service/search")
        if not data:
            error("Could not fetch services.")
            return

        svc_rows = data.get("rows", [])
        if not svc_rows:
            warn("No services found.")
            return

        choices = []
        for svc in svc_rows:
            name = svc.get("name", "?")
            description = svc.get("description", "")[:35]
            running = svc.get("running", 0)

            if running:
                icon = f"{C.GREEN}●{C.RESET}"
                status = "running"
            else:
                icon = f"{C.DIM}○{C.RESET}"
                status = "stopped"

            choices.append(f"{icon} {name:<25} {status:<10} {description}")

        choices.append("← Back")
        idx = pick_option("Services:", choices)
        if idx >= len(svc_rows):
            return

        svc = svc_rows[idx]
        svc_name = svc.get("name", "?")
        svc_id = svc.get("id", svc_name)
        _service_actions(svc_name, svc_id)


def _service_actions(svc_name, svc_id):
    """Actions for a specific service."""
    idx = pick_option(f"Service: {svc_name}", [
        "Restart service",
        "Stop service",
        "Start service",
        "← Back",
    ])
    if idx == 3:
        return
    elif idx == 0:
        if not confirm(f"Restart '{svc_name}'?", default_yes=False):
            return
        result = _api(f"/api/core/service/restart/{svc_id}", method="POST")
        if result is not None:
            success(f"Restarted: {svc_name}")
        else:
            error(f"Failed to restart {svc_name}.")
    elif idx == 1:
        if not confirm(f"Stop '{svc_name}'?", default_yes=False):
            return
        result = _api(f"/api/core/service/stop/{svc_id}", method="POST")
        if result is not None:
            success(f"Stopped: {svc_name}")
        else:
            error(f"Failed to stop {svc_name}.")
    elif idx == 2:
        result = _api(f"/api/core/service/start/{svc_id}", method="POST")
        if result is not None:
            success(f"Started: {svc_name}")
        else:
            error(f"Failed to start {svc_name}.")


def _vpn_status():
    """Show WireGuard and OpenVPN connection status."""
    print(f"\n  {C.BOLD}VPN Status{C.RESET}\n")

    any_data = False

    # WireGuard
    wg_data = _api("/api/wireguard/general/getStatus")
    if wg_data and isinstance(wg_data, dict):
        peers = wg_data.get("peers", wg_data.get("rows", []))
        if isinstance(peers, list) and peers:
            any_data = True
            print(f"  {C.BOLD}WireGuard Peers:{C.RESET}")
            for peer in peers:
                name = peer.get("name", peer.get("publicKey", "?")[:16])
                endpoint = peer.get("endpoint", "?")
                latest = peer.get("latestHandshake", peer.get("latest_handshake", ""))
                transfer_rx = peer.get("transferRx", peer.get("transfer_rx", 0))
                transfer_tx = peer.get("transferTx", peer.get("transfer_tx", 0))

                connected = bool(latest and str(latest) not in ("0", "(none)", ""))
                icon = f"{C.GREEN}●{C.RESET}" if connected else f"{C.DIM}○{C.RESET}"

                line = f"    {icon} {name:<25} {endpoint}"
                try:
                    rx_mb = int(transfer_rx) / (1024 ** 2)
                    tx_mb = int(transfer_tx) / (1024 ** 2)
                    if rx_mb > 0 or tx_mb > 0:
                        line += f"  {C.DIM}↓{rx_mb:.0f}MB ↑{tx_mb:.0f}MB{C.RESET}"
                except (ValueError, TypeError):
                    pass
                print(line)
            print()

    # OpenVPN
    ovpn_data = _api("/api/openvpn/service/searchSessions")
    if ovpn_data and isinstance(ovpn_data, dict):
        sessions = ovpn_data.get("rows", [])
        if sessions:
            any_data = True
            print(f"  {C.BOLD}OpenVPN Sessions:{C.RESET}")
            for s in sessions:
                name = s.get("common_name", s.get("username", "?"))
                real_addr = s.get("real_address", "?")
                connected_since = s.get("connected_since", "?")

                icon = f"{C.GREEN}●{C.RESET}"
                line = f"    {icon} {name:<25} {real_addr:<22} since {connected_since}"
                try:
                    rx = int(s.get("bytes_received", 0))
                    tx = int(s.get("bytes_sent", 0))
                    if rx > 0 or tx > 0:
                        rx_mb = rx / (1024 ** 2)
                        tx_mb = tx / (1024 ** 2)
                        line += f"  {C.DIM}↓{rx_mb:.0f}MB ↑{tx_mb:.0f}MB{C.RESET}"
                except (ValueError, TypeError):
                    pass
                print(line)
            print()

    if not any_data:
        warn("No VPN connections or tunnels found.")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
