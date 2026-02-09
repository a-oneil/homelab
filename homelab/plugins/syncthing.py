"""Syncthing plugin — folder sync status, connected devices, conflicts."""

import json
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to Syncthing."""
    base = CFG.get("syncthing_url", "").rstrip("/")
    token = CFG.get("syncthing_api_key", "")
    if not base or not token:
        return None

    url = f"{base}/rest{endpoint}"
    headers = {
        "X-API-Key": token,
        "Content-Type": "application/json",
    }

    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"Syncthing API error: {e}")
        return None


class SyncthingPlugin(Plugin):
    name = "Syncthing"
    key = "syncthing"

    def is_configured(self):
        return bool(CFG.get("syncthing_url") and CFG.get("syncthing_api_key"))

    def get_config_fields(self):
        return [
            ("syncthing_url", "Syncthing URL", "e.g. http://192.168.1.100:8384", False),
            ("syncthing_api_key", "Syncthing API Key", "from Settings > API Key", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_menu_items(self):
        return [
            ("Syncthing            — folder sync, devices, conflicts", syncthing_menu),
        ]

    def get_actions(self):
        return {
            "Syncthing Folders": ("syncthing_folders", _list_folders),
            "Syncthing Devices": ("syncthing_devices", _list_devices),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "syncthing_folder":
            folder_id = fav["id"]
            return lambda fid=folder_id: _folder_detail(fid)


def _fetch_stats():
    config = _api("/config")
    connections = _api("/system/connections")
    if config:
        folders = len(config.get("folders", []))
        devices = len(config.get("devices", []))
        connected = 0
        if connections:
            for dev_id, conn in connections.get("connections", {}).items():
                if conn.get("connected"):
                    connected += 1
        _HEADER_CACHE["stats"] = f"Syncthing: {folders} folders, {connected}/{devices} devices"
    _HEADER_CACHE["timestamp"] = time.time()


def syncthing_menu():
    while True:
        idx = pick_option("Syncthing:", [
            "Folders              — sync status and progress",
            "Devices              — connected peers",
            "Conflicts            — files with sync conflicts",
            "System Status        — version, uptime, connections",
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
            add_plugin_favorite(SyncthingPlugin())
        elif idx == 0:
            _list_folders()
        elif idx == 1:
            _list_devices()
        elif idx == 2:
            _show_conflicts()
        elif idx == 3:
            _system_status()


def _list_folders():
    """List all synced folders with status."""
    config = _api("/config")
    if not config:
        error("Could not fetch config.")
        return

    folders = config.get("folders", [])
    if not folders:
        warn("No folders configured.")
        return

    while True:
        choices = []
        for f in folders:
            fid = f.get("id", "?")
            label = f.get("label", fid)
            # Get folder status
            status = _api(f"/db/status?folder={fid}")
            if status:
                state = status.get("state", "?")
                completion = status.get("globalBytes", 0)
                need_bytes = status.get("needBytes", 0)
                if state == "idle":
                    icon = f"{C.GREEN}●{C.RESET}"
                elif state == "syncing":
                    icon = f"{C.YELLOW}●{C.RESET}"
                elif state == "error":
                    icon = f"{C.RED}●{C.RESET}"
                else:
                    icon = f"{C.DIM}○{C.RESET}"
                total_gb = completion / (1024 ** 3) if completion else 0
                need_mb = need_bytes / (1024 ** 2) if need_bytes else 0
                extra = f"  {C.YELLOW}need {need_mb:.1f}MB{C.RESET}" if need_bytes else ""
                choices.append(f"{icon} {label:<30} [{state}]  {total_gb:.1f}GB{extra}")
            else:
                choices.append(f"{C.DIM}○{C.RESET} {label:<30} [unknown]")

        choices.append("← Back")
        idx = pick_option("Folders:", choices)
        if idx >= len(folders):
            return

        _folder_detail(folders[idx].get("id", ""))


def _folder_detail(folder_id):
    """Show detail for a synced folder."""
    config = _api("/config")
    if not config:
        return

    folder = None
    for f in config.get("folders", []):
        if f.get("id") == folder_id:
            folder = f
            break
    if not folder:
        error(f"Folder {folder_id} not found.")
        return

    status = _api(f"/db/status?folder={folder_id}")
    label = folder.get("label", folder_id)
    path = folder.get("path", "?")

    print(f"\n  {C.BOLD}{label}{C.RESET}")
    print(f"  ID: {folder_id}")
    print(f"  Path: {path}")
    print(f"  Type: {folder.get('type', '?')}")

    if status:
        state = status.get("state", "?")
        global_files = status.get("globalFiles", 0)
        global_bytes = status.get("globalBytes", 0) / (1024 ** 3)
        need_files = status.get("needFiles", 0)
        need_bytes = status.get("needBytes", 0) / (1024 ** 2)
        errors = status.get("errors", 0)

        print(f"  State: {state}")
        print(f"  Files: {global_files:,}  ({global_bytes:.2f} GB)")
        if need_files:
            print(f"  {C.YELLOW}Need: {need_files} files ({need_bytes:.1f} MB){C.RESET}")
        if errors:
            print(f"  {C.RED}Errors: {errors}{C.RESET}")

    # Show shared devices
    devices = folder.get("devices", [])
    if devices:
        print(f"\n  {C.BOLD}Shared with:{C.RESET}")
        for d in devices:
            dev_id = d.get("deviceID", "?")[:12]
            print(f"    {dev_id}...")

    choices = ["Rescan", "★ Favorite", "← Back"]
    aidx = pick_option(f"Folder: {label}", choices)
    al = choices[aidx]

    if al == "← Back":
        return
    elif al == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("syncthing_folder", folder_id, f"Syncthing: {label}")
    elif al == "Rescan":
        result = _api(f"/db/scan?folder={folder_id}", method="POST")
        if result is not None:
            success(f"Rescan triggered for {label}")
        else:
            error("Failed to trigger rescan.")


def _list_devices():
    """List all connected devices."""
    config = _api("/config")
    connections = _api("/system/connections")
    if not config:
        error("Could not fetch config.")
        return

    devices = config.get("devices", [])
    conns = connections.get("connections", {}) if connections else {}

    print(f"\n  {C.BOLD}Syncthing Devices{C.RESET}\n")
    for d in devices:
        dev_id = d.get("deviceID", "?")
        name = d.get("name", dev_id[:12])
        conn = conns.get(dev_id, {})
        connected = conn.get("connected", False)
        address = conn.get("address", "?")

        icon = f"{C.GREEN}●{C.RESET}" if connected else f"{C.DIM}○{C.RESET}"
        if connected:
            in_bytes = conn.get("inBytesTotal", 0) / (1024 ** 2)
            out_bytes = conn.get("outBytesTotal", 0) / (1024 ** 2)
            traffic = f"  ↓{in_bytes:.0f}MB ↑{out_bytes:.0f}MB"
        else:
            traffic = ""

        print(f"  {icon} {name:<25} {C.DIM}{address}{C.RESET}{traffic}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _show_conflicts():
    """Show files with sync conflicts across all folders."""
    config = _api("/config")
    if not config:
        error("Could not fetch config.")
        return

    folders = config.get("folders", [])
    found_conflicts = False

    print(f"\n  {C.BOLD}Sync Conflicts{C.RESET}\n")

    for f in folders:
        fid = f.get("id", "?")
        label = f.get("label", fid)
        # Check for need items (which may include conflicts)
        status = _api(f"/db/status?folder={fid}")
        if status:
            errors = status.get("errors", 0)
            pull_errors = status.get("pullErrors", 0)
            if errors or pull_errors:
                print(f"  {C.RED}●{C.RESET} {label}: {errors} errors, {pull_errors} pull errors")
                found_conflicts = True

    if not found_conflicts:
        success("No conflicts found across any folder.")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _system_status():
    """Show Syncthing system info."""
    status = _api("/system/status")
    version = _api("/system/version")
    connections = _api("/system/connections")

    print(f"\n  {C.BOLD}Syncthing System Status{C.RESET}\n")

    if version:
        print(f"  {C.BOLD}Version:{C.RESET}  {version.get('version', '?')}")
        print(f"  {C.BOLD}OS:{C.RESET}       {version.get('os', '?')}/{version.get('arch', '?')}")

    if status:
        uptime = status.get("uptime", 0)
        hours = uptime // 3600
        mins = (uptime % 3600) // 60
        print(f"  {C.BOLD}Uptime:{C.RESET}   {hours}h {mins}m")
        print(f"  {C.BOLD}My ID:{C.RESET}    {status.get('myID', '?')[:12]}...")

    if connections:
        total = connections.get("total", {})
        in_rate = total.get("inBytesTotal", 0) / (1024 ** 3)
        out_rate = total.get("outBytesTotal", 0) / (1024 ** 3)
        print(f"  {C.BOLD}Total In:{C.RESET}  {in_rate:.2f} GB")
        print(f"  {C.BOLD}Total Out:{C.RESET} {out_rate:.2f} GB")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
