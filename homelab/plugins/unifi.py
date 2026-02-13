"""UniFi Network plugin — client lookup, device management, bandwidth."""

import http.cookiejar
import json
import ssl
import subprocess
import time
import urllib.request

from homelab.config import CFG
from homelab.modules.auditlog import log_action
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, confirm, prompt_text, info, success, error, warn

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


def _api_get(opener, base, endpoint, silent=False):
    """Make an authenticated GET request."""
    site = CFG.get("unifi_site", "default")
    url = f"{base}/api/s/{site}/{endpoint}"
    req = urllib.request.Request(url)
    try:
        with opener.open(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        if not silent:
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


def _api_put(opener, base, endpoint, data):
    """Make an authenticated PUT request."""
    site = CFG.get("unifi_site", "default")
    url = f"{base}/api/s/{site}/{endpoint}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
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
            ("unifi_ssh_user", "Device SSH User", "admin", False),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        try:
            opener, base = _get_session()
            if not opener:
                return []
            lines = []
            sta = _api_get(opener, base, "stat/sta", silent=True)
            if sta and sta.get("data"):
                lines.append(f"{len(sta['data'])} clients connected")
            dev = _api_get(opener, base, "stat/device", silent=True)
            if dev and dev.get("data"):
                devices = dev["data"]
                type_counts = {}
                for d in devices:
                    dtype = d.get("type", "unknown")
                    type_counts[dtype] = type_counts.get(dtype, 0) + 1
                parts = []
                type_labels = {"uap": "APs", "usw": "switches", "ugw": "gateways", "udm": "gateways"}
                for t, c in type_counts.items():
                    label = type_labels.get(t, t)
                    parts.append(f"{c} {label}")
                lines.append(f"{len(devices)} devices ({', '.join(parts)})")
            if not lines:
                return []
            return [{"title": "UniFi", "lines": lines}]
        except Exception:
            return []

    def get_health_alerts(self):
        try:
            opener, base = _get_session()
            if not opener:
                return [f"{C.RED}UniFi:{C.RESET} unreachable"]
            alerts = []
            dev = _api_get(opener, base, "stat/device", silent=True)
            if dev and dev.get("data"):
                for d in dev["data"]:
                    name = d.get("name", d.get("model", "?"))
                    if not d.get("adopted", True):
                        alerts.append(f"{C.YELLOW}UniFi:{C.RESET} {name} pending adoption")
                    elif d.get("state", 1) != 1:
                        alerts.append(f"{C.RED}UniFi:{C.RESET} {name} disconnected")
            return alerts
        except Exception:
            return [f"{C.RED}UniFi:{C.RESET} unreachable"]

    def get_menu_items(self):
        return [
            ("UniFi                — network clients and devices", unifi_menu),
        ]

    def get_actions(self):
        return {
            "UniFi Clients": ("unifi_clients", _show_clients),
            "UniFi Devices": ("unifi_devices", _show_devices),
            "UniFi Wireless": ("unifi_wireless", _wireless_analysis),
            "UniFi Firmware": ("unifi_firmware", _firmware_updates),
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
            "Clients               — active devices, bandwidth, block/unblock",
            "Network Devices       — APs, switches, gateways",
            "Wireless Analysis     — RF environment per AP",
            "Firmware Updates      — check and apply device updates",
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
            add_plugin_favorite(UnifiPlugin())
        elif idx == 0:
            _show_clients()
        elif idx == 1:
            _show_devices()
        elif idx == 2:
            _wireless_analysis()
        elif idx == 3:
            _firmware_updates()


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
    sort_by = "ip"
    while True:
        opener, base = _get_session()
        if not opener:
            return

        data = _api_get(opener, base, "stat/sta")
        if not data or not data.get("data"):
            warn("No active clients found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        all_clients = data["data"]
        if sort_by == "bandwidth":
            all_clients = sorted(all_clients, key=lambda c: c.get("tx_bytes", 0) + c.get("rx_bytes", 0), reverse=True)
        elif sort_by == "ip":
            all_clients = sorted(all_clients, key=lambda c: tuple(
                int(p) for p in c.get("ip", "0.0.0.0").split(".") if p.isdigit()
            ))
        else:
            all_clients = sorted(all_clients, key=lambda c: c.get("hostname", c.get("name", "zzz")).lower())

        choices = []
        clients = []
        for c in all_clients:
            hostname = c.get("hostname", c.get("name", "?"))[:20]
            ip = c.get("ip", "?")
            uptime = _format_uptime(c.get("uptime"))
            blocked = c.get("blocked", False)
            sig = c.get("signal")
            if blocked:
                dot = f"{C.RED}●{C.RESET}"
            elif sig:
                dot = f"{C.CYAN}●{C.RESET}"
            else:
                dot = f"{C.GREEN}●{C.RESET}"
            if sig:
                sig_val = int(sig)
                sig_color = C.GREEN if sig_val > -50 else C.YELLOW if sig_val > -70 else C.RED
                signal = f"{sig_color}{sig} dBm{C.RESET}"
            else:
                signal = f"{C.DIM}wired{C.RESET}"
            tx = c.get("tx_bytes", 0)
            rx = c.get("rx_bytes", 0)
            bw = ""
            if tx or rx:
                bw = f"  {C.DIM}↓{_format_bytes(rx)} ↑{_format_bytes(tx)}{C.RESET}"
            blocked_tag = f"  {C.RED}BLOCKED{C.RESET}" if blocked else ""
            choices.append(f"{dot} {hostname:<22} {ip:<16} {uptime:<10} {signal}{bw}{blocked_tag}")
            clients.append(c)

        client_count = len(choices)
        sort_next = {"name": "ip", "ip": "bandwidth", "bandwidth": "name"}
        sort_label = f"Sort: by {sort_next[sort_by]}  {C.DIM}(current: {sort_by}){C.RESET}"
        choices.append("───────────────")
        choices.append(sort_label)
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Clients ({client_count}):", choices)

        sep_idx = client_count
        sort_idx = client_count + 1
        refresh_idx = client_count + 2
        back_idx = client_count + 3

        if idx == back_idx:
            return
        elif idx == sep_idx:
            continue
        elif idx == sort_idx:
            sort_by = sort_next[sort_by]
        elif idx == refresh_idx:
            continue
        elif idx < client_count:
            _client_detail(clients[idx], opener, base)


def _client_detail(client, opener, base):
    """Show detail and actions for a client."""
    hostname = client.get("hostname", "?")
    alias = client.get("name", "")
    display_name = alias or hostname
    ip = client.get("ip", "?")
    mac = client.get("mac", "?")
    user_id = client.get("_id", "")
    uptime = _format_uptime(client.get("uptime"))
    blocked = client.get("blocked", False)
    sig = client.get("signal")
    tx = client.get("tx_bytes", 0)
    rx = client.get("rx_bytes", 0)

    print(f"\n  {C.BOLD}{display_name}{C.RESET}")
    if alias and alias != hostname:
        print(f"  {C.BOLD}Hostname:{C.RESET}  {hostname}")
    if alias:
        print(f"  {C.BOLD}Alias:{C.RESET}     {C.ACCENT}{alias}{C.RESET}")
    print(f"  {C.BOLD}IP:{C.RESET}        {ip}")
    print(f"  {C.BOLD}MAC:{C.RESET}       {mac}")
    print(f"  {C.BOLD}Uptime:{C.RESET}    {uptime}")
    if sig:
        print(f"  {C.BOLD}Signal:{C.RESET}    {sig} dBm")
    else:
        print(f"  {C.BOLD}Connection:{C.RESET} wired")
    if tx or rx:
        print(f"  {C.BOLD}Traffic:{C.RESET}   {C.GREEN}↓{C.RESET} {_format_bytes(rx)}  {C.ACCENT}↑{C.RESET} {_format_bytes(tx)}")
    if blocked:
        print(f"  {C.BOLD}Status:{C.RESET}    {C.RED}BLOCKED{C.RESET}")

    block_label = "Unblock client" if blocked else "Block client"
    alias_label = f"Set alias             — currently '{alias}'" if alias else "Set alias"
    aidx = pick_option(f"{display_name}:", [
        alias_label,
        block_label,
        "← Back",
    ])
    if aidx == 2:
        return
    elif aidx == 0:
        _set_alias(client, opener, base, display_name, user_id)
    elif aidx == 1:
        if blocked:
            action, cmd = "unblock", "unblock-sta"
        else:
            action, cmd = "block", "block-sta"
        if not confirm(f"{action.title()} '{display_name}' ({mac})?", default_yes=False):
            return
        result = _api_post(opener, base, "cmd/stamgr", {"cmd": cmd, "mac": mac})
        if result is not None:
            log_action(f"UniFi {action.title()} Client", display_name)
            success(f"Client {action}ed: {display_name}")
        else:
            error(f"Failed to {action} client.")


def _set_alias(client, opener, base, display_name, user_id):
    """Set or clear a client alias."""
    current = client.get("name", "")
    new_alias = prompt_text(f"Alias for {display_name}:", default=current)
    if new_alias is None:
        return
    new_alias = new_alias.strip()
    if new_alias == current:
        return
    if not user_id:
        error("Cannot set alias: client ID not found.")
        return
    result = _api_put(opener, base, f"rest/user/{user_id}", {"name": new_alias})
    if result is not None:
        label = f"'{new_alias}'" if new_alias else "cleared"
        log_action("UniFi Set Alias", f"{display_name} → {label}")
        success(f"Alias {label} for {display_name}")
    else:
        error("Failed to set alias.")


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
        type_labels = {"uap": "AP", "usw": "Switch", "ugw": "Gateway", "udm": "Gateway"}
        choices = []
        for d in devices:
            name = d.get("name", d.get("model", "?"))
            dtype = d.get("type", "?")
            ip = d.get("ip", "?")
            uptime = _format_uptime(d.get("uptime"))
            adopted = d.get("adopted", False)
            state = d.get("state", 0)
            if not adopted:
                dot = f"{C.YELLOW}●{C.RESET}"
                status = f"{C.YELLOW}pending{C.RESET}"
            elif state == 1:
                dot = f"{C.GREEN}●{C.RESET}"
                status = f"{C.GREEN}online{C.RESET}"
            else:
                dot = f"{C.RED}●{C.RESET}"
                status = f"{C.RED}offline{C.RESET}"
            tlabel = type_labels.get(dtype, dtype)
            choices.append(f"{dot} {name:<20} {C.DIM}{tlabel:<8}{C.RESET} {ip:<16} {status}  {C.DIM}Up: {uptime}{C.RESET}")

        count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Network Devices ({count}):", choices)
        if idx == count:
            continue
        elif idx == count + 1:
            continue
        elif idx == count + 2:
            return
        elif idx < count:
            _device_detail(devices[idx], opener, base)


def _device_detail(device, opener, base):
    """Show detail and actions for a network device."""
    name = device.get("name", device.get("model", "?"))
    dtype = device.get("type", "?")
    ip = device.get("ip", "?")
    mac = device.get("mac", "?")
    model = device.get("model", "?")
    version = device.get("version", "?")
    uptime = _format_uptime(device.get("uptime"))

    type_labels = {"uap": "Access Point", "usw": "Switch", "ugw": "Gateway", "udm": "Dream Machine"}
    type_label = type_labels.get(dtype, dtype)

    print(f"\n  {C.BOLD}{name}{C.RESET}  ({type_label})")
    print(f"  {C.BOLD}IP:{C.RESET}       {ip}")
    print(f"  {C.BOLD}MAC:{C.RESET}      {mac}")
    print(f"  {C.BOLD}Model:{C.RESET}    {model}")
    print(f"  {C.BOLD}Firmware:{C.RESET} {version}")
    print(f"  {C.BOLD}Uptime:{C.RESET}   {uptime}")

    upgradable = device.get("upgradable", False)
    upgrade_to = device.get("upgrade_to_firmware", "")
    if upgradable:
        print(f"  {C.YELLOW}Update available: {upgrade_to}{C.RESET}")

    ssh_user = CFG.get("unifi_ssh_user", "admin")

    action_items = [
        f"SSH to device          — ssh {ssh_user}@{ip}",
        "Restart device         — reboot this device",
    ]
    if dtype in ("usw",):
        action_items.append("Port Stats             — per-port link state and traffic")
    if upgradable:
        action_items.append(f"Upgrade Firmware       — update to {upgrade_to}")
    action_items.append("← Back")

    aidx = pick_option(f"{name}:", action_items)
    action = action_items[aidx]

    if action.startswith("←"):
        return
    elif action.startswith("SSH"):
        log_action("UniFi SSH to Device", f"{name} ({ip})")
        info(f"Connecting to {name} ({ip})... type 'exit' to quit.")
        try:
            subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", f"{ssh_user}@{ip}"])
        except KeyboardInterrupt:
            print()
    elif action.startswith("Restart"):
        if not confirm(f"Restart '{name}'? Device will be briefly offline.", default_yes=False):
            return
        result = _api_post(opener, base, "cmd/devmgr", {"cmd": "restart", "mac": mac})
        if result is not None:
            log_action("UniFi Restart Device", name)
            success(f"Restart initiated: {name}")
        else:
            error("Failed to restart device.")
    elif action.startswith("Port Stats"):
        _switch_port_stats(device)
    elif action.startswith("Upgrade"):
        if confirm(f"Upgrade {name} to firmware {upgrade_to}?", default_yes=False):
            result = _api_post(opener, base, "cmd/devmgr", {"cmd": "upgrade", "mac": mac})
            if result is not None:
                log_action("UniFi Firmware Upgrade", f"{name} → {upgrade_to}")
                success(f"Firmware upgrade initiated: {name}")
            else:
                error("Failed to trigger firmware upgrade.")


# ─── Wireless Analysis ────────────────────────────────────────────────────

def _wireless_analysis():
    """Show RF environment for each access point."""
    opener, base = _get_session()
    if not opener:
        return

    data = _api_get(opener, base, "stat/device")
    if not data or not data.get("data"):
        warn("No devices found.")
        return

    aps = [d for d in data["data"] if d.get("type") in ("uap",)]
    if not aps:
        warn("No access points found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Wireless Analysis{C.RESET}  ({len(aps)} APs)\n")

    for ap in aps:
        ap_name = ap.get("name", ap.get("model", "?"))
        print(f"  {C.BOLD}{ap_name}{C.RESET}")

        radio_table = ap.get("radio_table", [])
        radio_stats = ap.get("radio_table_stats", [])

        stats_map = {}
        for rs in radio_stats:
            radio_name = rs.get("name", "")
            stats_map[radio_name] = rs

        for radio in radio_table:
            radio_name = radio.get("name", "?")
            channel = radio.get("channel", "?")
            ht = radio.get("ht", "?")
            tx_power = radio.get("tx_power_mode", "auto")

            rs = stats_map.get(radio_name, {})
            cu_total = rs.get("cu_total", 0)
            cu_self_rx = rs.get("cu_self_rx", 0)
            cu_self_tx = rs.get("cu_self_tx", 0)
            satisfaction = rs.get("satisfaction", 0)
            num_sta = rs.get("num_sta", 0)

            if cu_total > 80:
                util_color = C.RED
            elif cu_total > 50:
                util_color = C.YELLOW
            else:
                util_color = C.GREEN

            band = "5GHz" if isinstance(channel, int) and channel > 14 else "2.4GHz"
            print(f"    {radio_name} ({band}) ch{channel} {ht}MHz")
            print(f"      Clients: {num_sta}  Satisfaction: {satisfaction}%")
            print(
                f"      Utilization: {util_color}{cu_total}%{C.RESET}  "
                f"(self tx: {cu_self_tx}%, self rx: {cu_self_rx}%)")
            print(f"      Tx power: {tx_power}")

        print()

    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Switch Port Stats ────────────────────────────────────────────────────

def _switch_port_stats(device):
    """Show per-port statistics for a switch."""
    name = device.get("name", "?")
    port_table = device.get("port_table", [])

    if not port_table:
        warn("No port data available.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}{name} — Switch Ports{C.RESET}\n")
    print(
        f"  {C.DIM}{'Port':<6} {'Name':<16} {'Link':<8} {'Speed':<12} "
        f"{'Rx':>12} {'Tx':>12} {'PoE':>6}{C.RESET}")
    print(f"  {'─' * 76}")

    for port in sorted(port_table, key=lambda p: p.get("port_idx", 0)):
        port_idx = port.get("port_idx", "?")
        port_name = port.get("name", "")
        is_up = port.get("up", False)
        speed = port.get("speed", 0)
        rx = port.get("rx_bytes", 0)
        tx = port.get("tx_bytes", 0)
        poe_enable = port.get("poe_enable", False)
        poe_power = port.get("poe_power", "")

        if is_up:
            link_str = f"{C.GREEN}UP{C.RESET}"
            speed_str = f"{speed} Mbps" if speed else "?"
        else:
            link_str = f"{C.DIM}down{C.RESET}"
            speed_str = f"{C.DIM}--{C.RESET}"

        poe_str = f"{poe_power}W" if poe_enable and poe_power else f"{C.DIM}--{C.RESET}"

        print(
            f"  {str(port_idx):<6} {port_name:<16} {link_str:<8} {speed_str:<12} "
            f"{_format_bytes(rx):>12} {_format_bytes(tx):>12} {poe_str:>6}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Firmware Updates ─────────────────────────────────────────────────────

def _firmware_updates():
    """Check all UniFi devices for available firmware updates."""
    opener, base = _get_session()
    if not opener:
        return

    data = _api_get(opener, base, "stat/device")
    if not data or not data.get("data"):
        warn("No devices found.")
        return

    devices = data["data"]
    upgradable = [d for d in devices if d.get("upgradable", False)]

    if not upgradable:
        success("All devices are up to date.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        for d in upgradable:
            dev_name = d.get("name", d.get("model", "?"))
            current = d.get("version", "?")
            upgrade_to = d.get("upgrade_to_firmware", "?")
            choices.append(f"{dev_name:<20} {current} → {C.GREEN}{upgrade_to}{C.RESET}")

        choices.extend([
            "───────────────",
            "Upgrade All",
            "← Back",
        ])

        hdr = f"\n  {C.BOLD}Firmware Updates{C.RESET}  ({len(upgradable)} device(s) with updates)\n"
        idx = pick_option("", choices, header=hdr)

        if choices[idx].startswith("←"):
            return
        elif choices[idx].startswith("─"):
            continue
        elif choices[idx] == "Upgrade All":
            if confirm(f"Upgrade firmware on {len(upgradable)} device(s)?", default_yes=False):
                for d in upgradable:
                    dev_name = d.get("name", d.get("model", "?"))
                    mac = d.get("mac", "")
                    upgrade_to = d.get("upgrade_to_firmware", "?")
                    info(f"Upgrading {dev_name}...")
                    result = _api_post(opener, base, "cmd/devmgr", {"cmd": "upgrade", "mac": mac})
                    if result is not None:
                        log_action("UniFi Firmware Upgrade", f"{dev_name} → {upgrade_to}")
                        success(f"  {dev_name}: upgrade initiated")
                    else:
                        error(f"  {dev_name}: upgrade failed")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
                return
        elif idx < len(upgradable):
            d = upgradable[idx]
            dev_name = d.get("name", d.get("model", "?"))
            mac = d.get("mac", "")
            upgrade_to = d.get("upgrade_to_firmware", "?")
            if confirm(f"Upgrade {dev_name} to {upgrade_to}?", default_yes=False):
                result = _api_post(opener, base, "cmd/devmgr", {"cmd": "upgrade", "mac": mac})
                if result is not None:
                    log_action("UniFi Firmware Upgrade", f"{dev_name} → {upgrade_to}")
                    success(f"Upgrade initiated: {dev_name}")
                else:
                    error("Upgrade failed.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
