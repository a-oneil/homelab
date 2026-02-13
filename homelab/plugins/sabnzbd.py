"""SABnzbd download client plugin — queue management, history, stats."""

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

from homelab.modules.auditlog import log_action
from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, confirm, prompt_text, info, success, error

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(mode, extra_params=None):
    """Call SABnzbd API: {url}/api?mode={mode}&apikey={key}&output=json."""
    url = CFG.get("sabnzbd_url", "").rstrip("/")
    key = CFG.get("sabnzbd_api_key", "")
    if not url or not key:
        return None
    params = f"mode={mode}&apikey={key}&output=json"
    if extra_params:
        params += "&" + extra_params
    req = urllib.request.Request(f"{url}/api?{params}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _api_raw(mode, extra_params=None):
    """Call SABnzbd API returning raw text (for simple ok/nok responses)."""
    url = CFG.get("sabnzbd_url", "").rstrip("/")
    key = CFG.get("sabnzbd_api_key", "")
    if not url or not key:
        return None
    params = f"mode={mode}&apikey={key}"
    if extra_params:
        params += "&" + extra_params
    req = urllib.request.Request(f"{url}/api?{params}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def _fetch_sabnzbd_stats():
    """Fetch stats for header display."""
    data = _api("queue")
    if data:
        q = data.get("queue", {})
        speed = q.get("speed", "0")
        status = q.get("status", "Idle")
        noofslots = int(q.get("noofslots", 0))
        if status == "Paused":
            _HEADER_CACHE["stats"] = "SABnzbd: paused"
        elif noofslots > 0 and speed != "0":
            _HEADER_CACHE["stats"] = f"SABnzbd: {speed}/s ↓ {noofslots} queued"
        else:
            _HEADER_CACHE["stats"] = "SABnzbd: idle"
    _HEADER_CACHE["timestamp"] = time.time()


def _format_size(size_str):
    """Format a size string, handling SABnzbd's various formats."""
    try:
        val = float(size_str)
        if val >= 1024:
            return f"{val / 1024:.1f} GB"
        return f"{val:.1f} MB"
    except (ValueError, TypeError):
        return str(size_str) if size_str else "?"


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

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_sabnzbd_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        data = _api("queue")
        if not data:
            return []
        q = data.get("queue", {})
        lines = []
        speed = q.get("speed", "0")
        status = q.get("status", "Idle")
        noofslots = int(q.get("noofslots", 0))
        sizeleft = q.get("sizeleft", "?")
        diskspace1 = q.get("diskspace1", "?")

        if status == "Paused":
            lines.append(f"{C.YELLOW}Paused{C.RESET}  {noofslots} in queue")
        elif noofslots > 0:
            lines.append(f"{C.GREEN}{speed}/s ↓{C.RESET}  {noofslots} in queue  {sizeleft} left")
        else:
            lines.append(f"{C.DIM}Idle — queue empty{C.RESET}")

        try:
            disk_gb = float(diskspace1)
            disk_color = C.RED if disk_gb < 10 else (C.YELLOW if disk_gb < 50 else C.GREEN)
            lines.append(f"Disk: {disk_color}{disk_gb:.1f} GB free{C.RESET}")
        except (ValueError, TypeError):
            pass

        return [{"title": "SABnzbd", "lines": lines}]

    def get_health_alerts(self):
        data = _api("queue")
        if data is None:
            return [f"{C.RED}SABnzbd:{C.RESET} unreachable"]
        return []

    def get_menu_items(self):
        return [
            ("SABnzbd              — downloads, queue, history, stats", sabnzbd_menu),
        ]

    def get_actions(self):
        return {
            "SABnzbd Queue": ("sabnzbd_queue", _queue_manager),
            "SABnzbd History": ("sabnzbd_history", _history),
            "SABnzbd Stats": ("sabnzbd_stats", _server_stats),
            "SABnzbd Add URL": ("sabnzbd_add_url", _add_url),
            "SABnzbd Upload File": ("sabnzbd_upload", _upload_file),
        }


def sabnzbd_menu():
    while True:
        idx = pick_option("SABnzbd:", [
            "Queue & Downloads    — view queue, pause/resume, reprioritize",
            "History              — completed and failed downloads",
            "Server Stats         — speed, disk space, status",
            "───────────────",
            "Add URL              — send a URL or NZB link",
            "Upload .nzb File     — upload from local machine",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 8:
            return
        elif idx in (3, 6):
            continue
        elif idx == 7:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(SabnzbdPlugin())
        elif idx == 0:
            _queue_manager()
        elif idx == 1:
            _history()
        elif idx == 2:
            _server_stats()
        elif idx == 4:
            _add_url()
        elif idx == 5:
            _upload_file()


def _queue_manager():
    """View and manage the SABnzbd download queue."""
    while True:
        data = _api("queue")
        if not data:
            error("Could not fetch SABnzbd queue.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        q = data.get("queue", {})
        slots = q.get("slots", [])
        status = q.get("status", "Idle")
        speed = q.get("speed", "0")
        paused = status == "Paused"

        # Build header
        pause_label = f"{C.YELLOW}PAUSED{C.RESET}" if paused else f"{C.GREEN}{speed}/s ↓{C.RESET}"
        header = (
            f"\n  {C.ACCENT}{C.BOLD}Download Queue{C.RESET}"
            f"  {len(slots)} items  |  {pause_label}\n"
        )

        choices = []
        # Global toggle at top
        if paused:
            choices.append(f"{C.GREEN}▶ Resume Queue{C.RESET}")
        else:
            choices.append(f"{C.YELLOW}⏸ Pause Queue{C.RESET}")

        choices.append("Speed Limit          — set download speed cap")
        choices.append("───────────────")

        for slot in slots:
            name = slot.get("filename", "?")
            pct = slot.get("percentage", "0")
            size = slot.get("size", "?")
            timeleft = slot.get("timeleft", "?")
            slot_status = slot.get("status", "?")

            if slot_status == "Downloading":
                icon = f"{C.GREEN}●{C.RESET}"
            elif slot_status == "Paused":
                icon = f"{C.YELLOW}●{C.RESET}"
            else:
                icon = f"{C.DIM}●{C.RESET}"

            display_name = name[:40] if len(name) > 40 else name
            choices.append(f"{icon} {display_name:<40} [{pct:>3}%]  {size:>8}  ETA {timeleft}")

        slot_count = len(slots)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option("Queue:", choices, header=header)

        sep1_idx = 2
        items_start = 3
        items_end = items_start + slot_count
        sep2_idx = items_end
        refresh_idx = items_end + 1
        back_idx = items_end + 2

        if idx == back_idx:
            return
        elif idx in (sep1_idx, sep2_idx):
            continue
        elif idx == refresh_idx:
            continue
        elif idx == 0:
            # Toggle pause/resume
            if paused:
                _api_raw("resume")
                success("Queue resumed.")
                log_action("SABnzbd Resume", "Queue resumed")
            else:
                _api_raw("pause")
                success("Queue paused.")
                log_action("SABnzbd Pause", "Queue paused")
        elif idx == 1:
            _speed_limit()
        elif items_start <= idx < items_end:
            _queue_item_actions(slots[idx - items_start])


def _queue_item_actions(slot):
    """Actions for a single queue item."""
    name = slot.get("filename", "?")
    nzo_id = slot.get("nzo_id", "")
    slot_status = slot.get("status", "?")
    pct = slot.get("percentage", "0")
    size = slot.get("size", "?")
    timeleft = slot.get("timeleft", "?")
    priority_map = {"-1": "Low", "0": "Normal", "1": "High", "2": "Force"}
    priority = priority_map.get(str(slot.get("priority", "0")), "Normal")

    print(f"\n  {C.BOLD}{name}{C.RESET}")
    print(f"  {C.BOLD}Status:{C.RESET}   {slot_status}")
    print(f"  {C.BOLD}Progress:{C.RESET} {pct}%")
    print(f"  {C.BOLD}Size:{C.RESET}     {size}")
    print(f"  {C.BOLD}ETA:{C.RESET}      {timeleft}")
    print(f"  {C.BOLD}Priority:{C.RESET} {priority}")

    is_paused = slot_status == "Paused"
    toggle_label = "Resume" if is_paused else "Pause"
    aidx = pick_option(f"{name[:50]}:", [
        f"{toggle_label}               — {'resume' if is_paused else 'pause'} this download",
        "Remove               — delete from queue",
        "Change Priority      — set High / Normal / Low / Force",
        "← Back",
    ])
    if aidx == 3:
        return
    elif aidx == 0:
        if is_paused:
            _api_raw("queue", f"name=resume&value={nzo_id}")
            success(f"Resumed: {name[:50]}")
            log_action("SABnzbd Resume Item", name[:60])
        else:
            _api_raw("queue", f"name=pause&value={nzo_id}")
            success(f"Paused: {name[:50]}")
            log_action("SABnzbd Pause Item", name[:60])
    elif aidx == 1:
        if confirm(f"Remove '{name[:50]}' from queue?", default_yes=False):
            _api_raw("queue", f"name=delete&value={nzo_id}")
            success(f"Removed: {name[:50]}")
            log_action("SABnzbd Remove", name[:60])
    elif aidx == 2:
        pidx = pick_option("Priority:", ["Force", "High", "Normal", "Low", "← Back"])
        pri_values = {"Force": "2", "High": "1", "Normal": "0", "Low": "-1"}
        pri_names = ["Force", "High", "Normal", "Low"]
        if pidx < 4:
            _api_raw("queue", f"name=priority&value={nzo_id}&value2={pri_values[pri_names[pidx]]}")
            success(f"Priority set to {pri_names[pidx]}: {name[:50]}")
            log_action("SABnzbd Priority", f"{pri_names[pidx]}: {name[:60]}")


def _speed_limit():
    """Set SABnzbd download speed limit."""
    data = _api("queue")
    current = "?"
    if data:
        current = data.get("queue", {}).get("speedlimit", "100")

    print(f"\n  Current speed limit: {C.BOLD}{current}%{C.RESET}")
    print(f"  {C.DIM}(100% = unlimited, 50% = half speed, 0 = pause){C.RESET}")

    idx = pick_option("Speed Limit:", [
        "Unlimited (100%)",
        "75%",
        "50%",
        "25%",
        "Custom",
        "← Back",
    ])
    if idx == 5:
        return

    limits = ["100", "75", "50", "25"]
    if idx < 4:
        value = limits[idx]
    else:
        value = prompt_text("Speed limit (% of max):")
        if not value:
            return

    _api_raw("config", f"name=speedlimit&value={value}")
    success(f"Speed limit set to {value}%")
    log_action("SABnzbd Speed Limit", f"{value}%")


def _history():
    """Show SABnzbd download history."""
    while True:
        data = _api("history", "limit=30")
        if not data:
            error("Could not fetch SABnzbd history.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        slots = data.get("history", {}).get("slots", [])
        if not slots:
            info("No download history.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for slot in slots:
            name = slot.get("name", "?")
            status = slot.get("status", "?")
            size = slot.get("size", "?")
            completed = slot.get("completed", 0)

            if completed:
                try:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(completed)
                    age = time.time() - completed
                    if age < 3600:
                        time_str = f"{int(age / 60)}m ago"
                    elif age < 86400:
                        time_str = f"{int(age / 3600)}h ago"
                    else:
                        time_str = dt.strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    time_str = "?"
            else:
                time_str = "?"

            if status == "Completed":
                icon = f"{C.GREEN}✓{C.RESET}"
            elif status == "Failed":
                icon = f"{C.RED}✗{C.RESET}"
            else:
                icon = f"{C.YELLOW}●{C.RESET}"

            display_name = name[:45] if len(name) > 45 else name
            choices.append(f"{icon} {display_name:<45} {size:>8}  {time_str:>10}")

        slot_count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"History ({slot_count}):", choices)
        if idx == slot_count + 2:
            return
        elif idx in (slot_count, slot_count + 1):
            continue
        else:
            _history_detail(slots[idx])


def _history_detail(slot):
    """Show detail for a history item."""
    name = slot.get("name", "?")
    status = slot.get("status", "?")
    size = slot.get("size", "?")
    category = slot.get("category", "?")
    storage = slot.get("storage", "?")
    nzo_id = slot.get("nzo_id", "")
    fail_message = slot.get("fail_message", "")

    print(f"\n  {C.BOLD}{name}{C.RESET}")
    print(f"  {C.BOLD}Status:{C.RESET}   {status}")
    print(f"  {C.BOLD}Size:{C.RESET}     {size}")
    print(f"  {C.BOLD}Category:{C.RESET} {category}")
    if storage:
        print(f"  {C.BOLD}Path:{C.RESET}     {storage}")
    if fail_message:
        print(f"  {C.BOLD}Error:{C.RESET}   {C.RED}{fail_message}{C.RESET}")

    stage_log = slot.get("stage_log", [])
    if stage_log:
        print(f"\n  {C.DIM}Stage Log:{C.RESET}")
        for stage in stage_log:
            stage_name = stage.get("name", "?")
            actions = stage.get("actions", [])
            for action in actions[:3]:
                print(f"  {C.DIM}  {stage_name}: {action}{C.RESET}")

    aidx = pick_option(f"{name[:50]}:", ["Delete from history", "← Back"])
    if aidx == 0:
        if confirm(f"Delete '{name[:50]}' from history?", default_yes=False):
            _api_raw("history", f"name=delete&value={nzo_id}")
            success(f"Deleted from history: {name[:50]}")
            log_action("SABnzbd Delete History", name[:60])


def _server_stats():
    """Show SABnzbd server status and stats."""
    data = _api("queue")
    if not data:
        error("Could not fetch SABnzbd status.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    q = data.get("queue", {})
    status = q.get("status", "?")
    speed = q.get("speed", "0")
    speedlimit = q.get("speedlimit", "100")
    sizeleft = q.get("sizeleft", "?")
    timeleft = q.get("timeleft", "?")
    noofslots = q.get("noofslots", 0)
    diskspace1 = q.get("diskspace1", "?")
    diskspace2 = q.get("diskspace2", "?")
    diskspacetotal1 = q.get("diskspacetotal1", "?")
    diskspacetotal2 = q.get("diskspacetotal2", "?")
    version = q.get("version", "?")

    # Color for status
    if status == "Downloading":
        status_color = C.GREEN
    elif status == "Paused":
        status_color = C.YELLOW
    else:
        status_color = C.DIM

    print(f"\n  {C.BOLD}SABnzbd Server Status{C.RESET}\n")
    print(f"  {C.BOLD}Version:{C.RESET}     {version}")
    print(f"  {C.BOLD}Status:{C.RESET}      {status_color}{status}{C.RESET}")
    print(f"  {C.BOLD}Speed:{C.RESET}       {speed}/s")
    print(f"  {C.BOLD}Speed Limit:{C.RESET} {speedlimit}%")
    print(f"  {C.BOLD}Queue:{C.RESET}       {noofslots} items, {sizeleft} remaining")
    print(f"  {C.BOLD}Time Left:{C.RESET}   {timeleft}")

    # Disk space
    print(f"\n  {C.BOLD}Disk Space{C.RESET}")
    try:
        free1 = float(diskspace1)
        total1 = float(diskspacetotal1)
        disk_color = C.RED if free1 < 10 else (C.YELLOW if free1 < 50 else C.GREEN)
        print(f"  Temp:       {disk_color}{free1:.1f} GB free{C.RESET} / {total1:.1f} GB")
    except (ValueError, TypeError):
        print(f"  Temp:       {diskspace1}")
    try:
        free2 = float(diskspace2)
        total2 = float(diskspacetotal2)
        disk_color = C.RED if free2 < 10 else (C.YELLOW if free2 < 50 else C.GREEN)
        print(f"  Complete:   {disk_color}{free2:.1f} GB free{C.RESET} / {total2:.1f} GB")
    except (ValueError, TypeError):
        if diskspace2 and diskspace2 != diskspace1:
            print(f"  Complete:   {diskspace2}")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


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
    result = _api_raw("addurl", f"name={encoded}")
    if result and ("ok" in result.lower() or "nzo_id" in result.lower()):
        log_action("SABnzbd Add URL", link[:60])
        success(f"Sent to SABnzbd: {link[:60]}")
        from homelab.notifications import notify
        notify("Homelab", "Download added to SABnzbd")
    else:
        error(f"SABnzbd response: {result}")


def _upload_file():
    """Upload a local .nzb file to SABnzbd."""
    from homelab.modules.files import browse_local

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
        log_action("SABnzbd Upload", os.path.basename(filepath))
        success(f"Uploaded to SABnzbd: {os.path.basename(filepath)}")
    else:
        error(f"SABnzbd upload failed: {result.stdout.strip()}")
