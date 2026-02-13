"""Tailscale plugin — list devices, check connectivity, manage exit nodes."""

import json
import time

from homelab.config import CFG
from homelab.modules.auditlog import log_action
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, confirm, info, success, error

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _get_host():
    return CFG.get("unraid_ssh_host", "")


def _ts_cmd(args, **kwargs):
    """Run a tailscale CLI command via SSH."""
    from homelab.modules.transport import ssh_run
    return ssh_run(f"tailscale {args}", host=_get_host(), **kwargs)


def _ts_status(background=False):
    """Get tailscale status as JSON."""
    result = _ts_cmd("status --json", background=background)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


class TailscalePlugin(Plugin):
    name = "Tailscale"
    key = "tailscale"

    def is_configured(self):
        return bool(CFG.get("tailscale_enabled"))

    def get_config_fields(self):
        return [
            ("tailscale_enabled", "Tailscale Enabled", "set to 'true' to enable", False),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_menu_items(self):
        return [
            ("Tailscale            — devices, connectivity, exit nodes", ts_menu),
        ]

    def get_actions(self):
        return {
            "Tailscale Devices": ("ts_devices", _list_devices),
            "Tailscale Ping": ("ts_ping", _ping_device),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "ts_device":
            hostname = fav["id"]
            return lambda h=hostname: _device_detail(h)


def _fetch_stats():
    status = _ts_status(background=True)
    if status:
        peers = status.get("Peer", {})
        online = sum(1 for p in peers.values() if p.get("Online"))
        total = len(peers)
        _HEADER_CACHE["stats"] = f"Tailscale: {online}/{total} online"
    _HEADER_CACHE["timestamp"] = time.time()


def ts_menu():
    while True:
        idx = pick_option("Tailscale:", [
            "Devices              — list all devices with status",
            "Exit Nodes           — view and set exit node",
            "Ping Device          — check latency to a peer",
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
            add_plugin_favorite(TailscalePlugin())
        elif idx == 0:
            _list_devices()
        elif idx == 1:
            _exit_nodes()
        elif idx == 2:
            _ping_device()


def _list_devices():
    """List all Tailscale peers."""
    status = _ts_status()
    if not status:
        error("Could not get Tailscale status.")
        return

    self_node = status.get("Self", {})
    peers = status.get("Peer", {})

    # Show self first
    print(f"\n  {C.BOLD}This Node:{C.RESET}")
    self_name = self_node.get("HostName", "?")
    self_ip = self_node.get("TailscaleIPs", ["?"])[0] if self_node.get("TailscaleIPs") else "?"
    print(f"  {C.GREEN}●{C.RESET} {self_name:<25} {self_ip}")
    print()

    # Build peer list
    devices = []
    choices = []
    for key, peer in sorted(peers.items(), key=lambda x: x[1].get("HostName", "")):
        hostname = peer.get("HostName", "?")
        online = peer.get("Online", False)
        ips = peer.get("TailscaleIPs", [])
        ip = ips[0] if ips else "?"
        os_name = peer.get("OS", "?")
        exit_node = peer.get("ExitNode", False)

        icon = f"{C.GREEN}●{C.RESET}" if online else f"{C.DIM}○{C.RESET}"
        extra = f" {C.YELLOW}[EXIT]{C.RESET}" if exit_node else ""
        choices.append(f"{icon} {hostname:<25} {ip:<18} {os_name}{extra}")
        devices.append({"hostname": hostname, "ip": ip, "online": online, "key": key})

    choices.append("← Back")
    idx = pick_option("Tailscale Devices:", choices)
    if idx < len(devices):
        _device_detail(devices[idx]["hostname"])


def _device_detail(hostname):
    """Show detail for a Tailscale device."""
    status = _ts_status()
    if not status:
        return

    peers = status.get("Peer", {})
    peer = None
    for p in peers.values():
        if p.get("HostName") == hostname:
            peer = p
            break

    if not peer:
        error(f"Device {hostname} not found.")
        return

    online = peer.get("Online", False)
    ips = peer.get("TailscaleIPs", [])
    os_name = peer.get("OS", "?")
    exit_node = peer.get("ExitNode", False)
    last_seen = peer.get("LastSeen", "?")
    rx = peer.get("RxBytes", 0)
    tx = peer.get("TxBytes", 0)

    print(f"\n  {C.BOLD}{hostname}{C.RESET}")
    print(f"  Status: {C.GREEN}Online{C.RESET}" if online else f"  Status: {C.DIM}Offline{C.RESET}")
    print(f"  IPs: {', '.join(ips)}")
    print(f"  OS: {os_name}")
    print(f"  Exit Node: {'Yes' if exit_node else 'No'}")
    print(f"  Last Seen: {last_seen}")
    if rx or tx:
        rx_mb = rx / (1024 * 1024)
        tx_mb = tx / (1024 * 1024)
        print(f"  Traffic: {C.GREEN}↓{rx_mb:.1f}MB{C.RESET} {C.ACCENT}↑{tx_mb:.1f}MB{C.RESET}")

    action_choices = ["Ping", "★ Favorite", "← Back"]
    if not exit_node:
        action_choices.insert(1, "Set as Exit Node")

    aidx = pick_option(f"Device: {hostname}", action_choices)
    al = action_choices[aidx]

    if al == "← Back":
        return
    elif al == "Ping":
        info(f"Pinging {hostname}...")
        result = _ts_cmd(f"ping --c 4 {hostname}")
        if result.returncode == 0:
            log_action("Tailscale Ping", hostname)
            print(f"\n{result.stdout}")
        else:
            error("Ping failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif al == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("ts_device", hostname, f"Tailscale: {hostname}")
    elif al == "Set as Exit Node":
        if confirm(f"Route all traffic through {hostname}?"):
            r = _ts_cmd(f"set --exit-node={hostname}")
            if r.returncode == 0:
                log_action("Tailscale Exit Node", hostname)
                success(f"Exit node set to {hostname}")
            else:
                error("Failed to set exit node.")


def _ping_device():
    """Ping a Tailscale peer."""
    status = _ts_status()
    if not status:
        error("Could not get Tailscale status.")
        return

    peers = status.get("Peer", {})
    devices = []
    choices = []
    for peer in sorted(peers.values(), key=lambda x: x.get("HostName", "")):
        hostname = peer.get("HostName", "?")
        online = peer.get("Online", False)
        icon = f"{C.GREEN}●{C.RESET}" if online else f"{C.DIM}○{C.RESET}"
        choices.append(f"{icon} {hostname}")
        devices.append(hostname)

    choices.append("← Back")
    idx = pick_option("Ping which device?", choices)
    if idx >= len(devices):
        return

    hostname = devices[idx]
    info(f"Pinging {hostname}...")
    result = _ts_cmd(f"ping --c 4 {hostname}")
    if result.returncode == 0:
        log_action("Tailscale Ping", hostname)
        print(f"\n{result.stdout}")
    else:
        error("Ping failed.")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _exit_nodes():
    """View and manage exit nodes."""
    status = _ts_status()
    if not status:
        error("Could not get Tailscale status.")
        return

    peers = status.get("Peer", {})
    current_exit = None
    available = []

    for peer in peers.values():
        if peer.get("ExitNode"):
            current_exit = peer.get("HostName", "?")
        if peer.get("ExitNodeOption"):
            available.append(peer.get("HostName", "?"))

    print(f"\n  {C.BOLD}Exit Nodes{C.RESET}\n")
    if current_exit:
        print(f"  Current: {C.GREEN}{current_exit}{C.RESET}")
    else:
        print(f"  Current: {C.DIM}None{C.RESET}")

    if not available:
        print(f"  {C.DIM}No exit node options available.{C.RESET}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    choices = available + ["Disable exit node", "← Back"]
    idx = pick_option("Set exit node:", choices)
    if idx >= len(available) + 1:
        return
    elif idx == len(available):
        # Disable
        r = _ts_cmd("set --exit-node=")
        if r.returncode == 0:
            log_action("Tailscale Exit Node Disabled", "")
            success("Exit node disabled.")
        else:
            error("Failed to disable exit node.")
    else:
        hostname = available[idx]
        r = _ts_cmd(f"set --exit-node={hostname}")
        if r.returncode == 0:
            log_action("Tailscale Exit Node", hostname)
            success(f"Exit node set to {hostname}")
        else:
            error("Failed to set exit node.")
