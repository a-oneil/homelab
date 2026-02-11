"""Deluge download client plugin — torrent management, stats, speed control."""

import base64
import json
import os
import time
import urllib.request
from http.cookiejar import CookieJar

from homelab.auditlog import log_action
from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, confirm, prompt_text, info, success, error

# Session-aware opener that preserves cookies across RPC calls
_cookie_jar = CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _rpc(method, params=None):
    """Make a JSON-RPC call to Deluge (uses shared cookie jar for session)."""
    url = CFG.get("deluge_url", "").rstrip("/")
    if not url:
        return None
    payload = json.dumps({
        "method": method,
        "params": params or [],
        "id": 1,
    }).encode()
    req = urllib.request.Request(
        f"{url}/json",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = _opener.open(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def _auth_rpc(method, params=None):
    """Authenticate and make an RPC call."""
    pw = CFG.get("deluge_password", "")
    auth = _rpc("auth.login", [pw])
    if not auth or not auth.get("result"):
        return None
    return _rpc(method, params)


def _set_label(torrent_id):
    """Set the 'homelab' label on a torrent via the Label plugin."""
    _rpc("label.add", ["homelab"])
    _rpc("label.set_torrent", [torrent_id, "homelab"])


def _format_speed(bytes_per_sec):
    """Format bytes/sec to human-readable speed."""
    if not bytes_per_sec or bytes_per_sec < 0:
        return "0 B/s"
    if bytes_per_sec >= 1048576:
        return f"{bytes_per_sec / 1048576:.1f} MB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{int(bytes_per_sec)} B/s"


def _format_size(size_bytes):
    """Format bytes to human-readable size."""
    if not size_bytes or size_bytes < 0:
        return "0 B"
    if size_bytes >= 1073741824:
        return f"{size_bytes / 1073741824:.1f} GB"
    if size_bytes >= 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{int(size_bytes)} B"


def _format_eta(seconds):
    """Format seconds to human-readable ETA."""
    if not seconds or seconds < 0:
        return "\u221e"
    if seconds >= 86400:
        return f"{int(seconds / 86400)}d {int((seconds % 86400) / 3600)}h"
    if seconds >= 3600:
        return f"{int(seconds / 3600)}h {int((seconds % 3600) / 60)}m"
    if seconds >= 60:
        return f"{int(seconds / 60)}m {int(seconds % 60)}s"
    return f"{int(seconds)}s"


def _fetch_deluge_stats():
    """Fetch stats for header display."""
    result = _auth_rpc("core.get_session_status", [["download_rate", "upload_rate", "num_peers"]])
    torrents = _auth_rpc("core.get_torrents_status", [{}, ["state"]])
    if result and result.get("result") is not None:
        stats = result["result"]
        dl_rate = stats.get("download_rate", 0)
        active = 0
        if torrents and isinstance(torrents.get("result"), dict):
            active = sum(1 for t in torrents["result"].values() if t.get("state") == "Downloading")
        if dl_rate > 1024:
            _HEADER_CACHE["stats"] = f"Deluge: {_format_speed(dl_rate)} \u2193 {active} active"
        elif active:
            _HEADER_CACHE["stats"] = f"Deluge: {active} active"
        else:
            _HEADER_CACHE["stats"] = "Deluge: idle"
    _HEADER_CACHE["timestamp"] = time.time()


class DelugePlugin(Plugin):
    name = "Deluge"
    key = "deluge"

    def is_configured(self):
        return bool(CFG.get("deluge_url"))

    def get_config_fields(self):
        return [
            ("deluge_url", "Deluge URL", "e.g. http://192.168.1.100:8112", False),
            ("deluge_password", "Deluge Password", "Web UI password", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_deluge_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        result = _auth_rpc("core.get_session_status", [
            ["download_rate", "upload_rate", "total_download", "total_upload"]
        ])
        torrents = _auth_rpc("core.get_torrents_status", [{}, ["state"]])
        if not result or result.get("result") is None:
            return []

        stats = result["result"]
        lines = []
        dl = stats.get("download_rate", 0)
        ul = stats.get("upload_rate", 0)

        downloading = 0
        seeding = 0
        if torrents and isinstance(torrents.get("result"), dict):
            for t in torrents["result"].values():
                state = t.get("state", "")
                if state == "Downloading":
                    downloading += 1
                elif state == "Seeding":
                    seeding += 1

        if dl > 0 or ul > 0:
            lines.append(f"{C.GREEN}{_format_speed(dl)} \u2193{C.RESET}  {C.CYAN}{_format_speed(ul)} \u2191{C.RESET}")
        if downloading or seeding:
            parts = []
            if downloading:
                parts.append(f"{downloading} downloading")
            if seeding:
                parts.append(f"{seeding} seeding")
            lines.append(", ".join(parts))
        if not lines:
            lines.append(f"{C.DIM}Idle{C.RESET}")

        return [{"title": "Deluge", "lines": lines}]

    def get_health_alerts(self):
        result = _rpc("auth.login", [CFG.get("deluge_password", "")])
        if result is None:
            return [f"{C.RED}Deluge:{C.RESET} unreachable"]
        return []

    def get_menu_items(self):
        return [
            ("Deluge               \u2014 torrents, stats, speed control", deluge_menu),
        ]

    def get_actions(self):
        return {
            "Deluge Torrents": ("deluge_torrents", _torrent_list),
            "Deluge Stats": ("deluge_stats", _transfer_stats),
            "Deluge Add URL": ("deluge_add_url", _add_url),
            "Deluge Upload File": ("deluge_upload", _upload_file),
        }


def deluge_menu():
    while True:
        idx = pick_option("Deluge:", [
            "Torrents             \u2014 active downloads with progress and speed",
            "Transfer Stats       \u2014 DL/UL speed, ratio, active peers",
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "Add URL / Magnet     \u2014 send a URL or magnet link",
            "Upload .torrent File \u2014 upload from local machine",
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "\u2605 Add to Favorites   \u2014 pin an action to the main menu",
            "\u2190 Back",
        ])
        if idx == 7:
            return
        elif idx in (2, 5):
            continue
        elif idx == 6:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(DelugePlugin())
        elif idx == 0:
            _torrent_list()
        elif idx == 1:
            _transfer_stats()
        elif idx == 3:
            _add_url()
        elif idx == 4:
            _upload_file()


def _torrent_list():
    """View and manage active torrents."""
    while True:
        keys = [
            "name", "state", "progress", "download_payload_rate",
            "upload_payload_rate", "eta", "ratio", "total_size",
            "total_done", "num_seeds", "num_peers", "label",
        ]
        result = _auth_rpc("core.get_torrents_status", [{}, keys])
        if not result or result.get("result") is None:
            error("Could not fetch torrent list.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        torrents = result["result"]
        if not torrents:
            info("No torrents.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        # Sort by state priority then name
        state_order = {"Downloading": 0, "Seeding": 1, "Paused": 2, "Checking": 3, "Queued": 4, "Error": 5}
        sorted_ids = sorted(
            torrents.keys(),
            key=lambda tid: (state_order.get(torrents[tid].get("state", ""), 9), torrents[tid].get("name", "").lower())
        )

        # Count by state
        state_counts = {}
        for tid in sorted_ids:
            state = torrents[tid].get("state", "?")
            state_counts[state] = state_counts.get(state, 0) + 1

        summary_parts = []
        for state in ["Downloading", "Seeding", "Paused", "Checking", "Error"]:
            if state_counts.get(state):
                summary_parts.append(f"{state_counts[state]} {state.lower()}")

        header = (
            f"\n  {C.ACCENT}{C.BOLD}Torrents{C.RESET}"
            f"  {len(torrents)} total"
        )
        if summary_parts:
            header += f"  |  {', '.join(summary_parts)}"
        header += "\n"

        choices = []
        torrent_ids = []
        for tid in sorted_ids:
            t = torrents[tid]
            name = t.get("name", "?")
            state = t.get("state", "?")
            progress = t.get("progress", 0)
            dl_rate = t.get("download_payload_rate", 0)
            ul_rate = t.get("upload_payload_rate", 0)
            eta = t.get("eta", 0)

            # State icon/color
            if state == "Downloading":
                icon = f"{C.CYAN}\u25cf{C.RESET}"
            elif state == "Seeding":
                icon = f"{C.GREEN}\u25cf{C.RESET}"
            elif state == "Paused":
                icon = f"{C.YELLOW}\u25cf{C.RESET}"
            elif state == "Error":
                icon = f"{C.RED}\u25cf{C.RESET}"
            else:
                icon = f"{C.DIM}\u25cf{C.RESET}"

            display_name = name[:35] if len(name) > 35 else name

            # Build info string
            parts = [f"[{progress:.1f}%]"]
            if dl_rate > 0:
                parts.append(f"{_format_speed(dl_rate)} \u2193")
            if ul_rate > 0:
                parts.append(f"{_format_speed(ul_rate)} \u2191")
            if state == "Downloading" and eta and eta > 0:
                parts.append(f"ETA {_format_eta(eta)}")

            info_str = "  ".join(parts)
            choices.append(f"{icon} {display_name:<35} {info_str}")
            torrent_ids.append(tid)

        torrent_count = len(choices)
        choices.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        choices.append("\u21bb Refresh")
        choices.append("\u2190 Back")

        idx = pick_option("Torrents:", choices, header=header)
        if idx == torrent_count + 2:
            return
        elif idx in (torrent_count, torrent_count + 1):
            continue
        elif idx < torrent_count:
            _torrent_actions(torrent_ids[idx], torrents[torrent_ids[idx]])


def _torrent_actions(torrent_id, torrent):
    """Actions for a single torrent."""
    name = torrent.get("name", "?")
    state = torrent.get("state", "?")
    progress = torrent.get("progress", 0)
    total_size = torrent.get("total_size", 0)
    total_done = torrent.get("total_done", 0)
    ratio = torrent.get("ratio", 0)
    seeds = torrent.get("num_seeds", 0)
    peers = torrent.get("num_peers", 0)
    dl_rate = torrent.get("download_payload_rate", 0)
    ul_rate = torrent.get("upload_payload_rate", 0)

    print(f"\n  {C.BOLD}{name}{C.RESET}")
    print(f"  {C.BOLD}State:{C.RESET}    {state}")
    print(f"  {C.BOLD}Progress:{C.RESET} {progress:.1f}%  ({_format_size(total_done)} / {_format_size(total_size)})")
    print(f"  {C.BOLD}Ratio:{C.RESET}    {ratio:.2f}")
    print(f"  {C.BOLD}Seeds:{C.RESET}    {seeds}   {C.BOLD}Peers:{C.RESET} {peers}")
    if dl_rate > 0 or ul_rate > 0:
        print(f"  {C.BOLD}Speed:{C.RESET}    {_format_speed(dl_rate)} \u2193  {_format_speed(ul_rate)} \u2191")

    is_paused = state == "Paused"
    toggle_label = "Resume" if is_paused else "Pause"
    aidx = pick_option(f"{name[:50]}:", [
        f"{toggle_label}               \u2014 {'resume' if is_paused else 'pause'} this torrent",
        "Remove (keep data)   \u2014 remove torrent, keep files",
        "Remove (delete data) \u2014 remove torrent and files",
        "Force Recheck        \u2014 verify downloaded data",
        "\u2190 Back",
    ])
    if aidx == 4:
        return
    elif aidx == 0:
        if is_paused:
            _auth_rpc("core.resume_torrent", [[torrent_id]])
            success(f"Resumed: {name[:50]}")
            log_action("Deluge Resume", name[:60])
        else:
            _auth_rpc("core.pause_torrent", [[torrent_id]])
            success(f"Paused: {name[:50]}")
            log_action("Deluge Pause", name[:60])
    elif aidx == 1:
        if confirm(f"Remove '{name[:50]}' (keep files)?", default_yes=False):
            _auth_rpc("core.remove_torrent", [torrent_id, False])
            success(f"Removed: {name[:50]}")
            log_action("Deluge Remove", name[:60])
    elif aidx == 2:
        if confirm(f"Remove '{name[:50]}' AND delete files?", default_yes=False):
            _auth_rpc("core.remove_torrent", [torrent_id, True])
            success(f"Removed with data: {name[:50]}")
            log_action("Deluge Remove+Delete", name[:60])
    elif aidx == 3:
        _auth_rpc("core.force_recheck", [[torrent_id]])
        success(f"Recheck started: {name[:50]}")
        log_action("Deluge Recheck", name[:60])


def _transfer_stats():
    """Show Deluge transfer statistics and speed controls."""
    result = _auth_rpc("core.get_session_status", [[
        "download_rate", "upload_rate", "total_download", "total_upload",
        "num_peers", "dht_nodes", "payload_download_rate", "payload_upload_rate",
    ]])
    config = _auth_rpc("core.get_config")

    if not result or result.get("result") is None:
        error("Could not fetch Deluge stats.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    stats = result["result"]
    dl_rate = stats.get("download_rate", 0)
    ul_rate = stats.get("upload_rate", 0)
    total_dl = stats.get("total_download", 0)
    total_ul = stats.get("total_upload", 0)
    peers = stats.get("num_peers", 0)
    dht = stats.get("dht_nodes", 0)

    max_dl = -1
    max_ul = -1
    if config and config.get("result") is not None:
        cfg = config["result"]
        max_dl = cfg.get("max_download_speed", -1)
        max_ul = cfg.get("max_upload_speed", -1)

    print(f"\n  {C.BOLD}Deluge Transfer Statistics{C.RESET}\n")
    print(f"  {C.BOLD}Download:{C.RESET}    {C.GREEN}{_format_speed(dl_rate)}{C.RESET}")
    print(f"  {C.BOLD}Upload:{C.RESET}      {C.CYAN}{_format_speed(ul_rate)}{C.RESET}")
    print(f"  {C.BOLD}Total DL:{C.RESET}    {_format_size(total_dl)}")
    print(f"  {C.BOLD}Total UL:{C.RESET}    {_format_size(total_ul)}")
    if total_dl > 0:
        overall_ratio = total_ul / total_dl
        print(f"  {C.BOLD}Ratio:{C.RESET}       {overall_ratio:.2f}")
    print(f"\n  {C.BOLD}Peers:{C.RESET}       {peers}")
    print(f"  {C.BOLD}DHT Nodes:{C.RESET}   {dht}")

    dl_limit_str = f"{max_dl:.0f} KB/s" if max_dl > 0 else "Unlimited"
    ul_limit_str = f"{max_ul:.0f} KB/s" if max_ul > 0 else "Unlimited"
    print(f"\n  {C.BOLD}DL Limit:{C.RESET}    {dl_limit_str}")
    print(f"  {C.BOLD}UL Limit:{C.RESET}    {ul_limit_str}")

    aidx = pick_option("Speed Control:", [
        "Set Download Limit   \u2014 cap download speed",
        "Set Upload Limit     \u2014 cap upload speed",
        "Remove All Limits    \u2014 set unlimited",
        "\u2190 Back",
    ])
    if aidx == 3:
        return
    elif aidx == 0:
        val = prompt_text("Download limit (KB/s, 0=unlimited):")
        if val:
            try:
                limit = float(val)
                _auth_rpc("core.set_config", [{"max_download_speed": limit if limit > 0 else -1}])
                success(f"Download limit set to {val} KB/s" if limit > 0 else "Download limit removed")
                log_action("Deluge DL Limit", f"{val} KB/s")
            except ValueError:
                error("Invalid number.")
    elif aidx == 1:
        val = prompt_text("Upload limit (KB/s, 0=unlimited):")
        if val:
            try:
                limit = float(val)
                _auth_rpc("core.set_config", [{"max_upload_speed": limit if limit > 0 else -1}])
                success(f"Upload limit set to {val} KB/s" if limit > 0 else "Upload limit removed")
                log_action("Deluge UL Limit", f"{val} KB/s")
            except ValueError:
                error("Invalid number.")
    elif aidx == 2:
        _auth_rpc("core.set_config", [{"max_download_speed": -1, "max_upload_speed": -1}])
        success("All speed limits removed.")
        log_action("Deluge Speed Limit", "Unlimited")


def _add_url():
    """Send a URL or magnet link to Deluge."""
    url = CFG.get("deluge_url", "")
    if not url:
        error("Deluge not configured. Set URL in Settings.")
        return

    link = prompt_text("URL or magnet link:")
    if not link:
        return

    pw = CFG.get("deluge_password", "")
    auth = _rpc("auth.login", [pw])
    if not auth or not auth.get("result"):
        error("Deluge authentication failed.")
        return

    if link.startswith("magnet:"):
        result = _rpc("core.add_torrent_magnet", [link, {}])
    else:
        result = _rpc("core.add_torrent_url", [link, {}])

    if result and result.get("result"):
        _set_label(result["result"])
        log_action("Deluge Add URL", link[:60])
        success(f"Sent to Deluge: {link[:60]}")
        from homelab.notifications import notify
        notify("Homelab", "Download added to Deluge")
    else:
        err = result.get("error", {}).get("message", "Unknown error") if result else "No response"
        error(f"Deluge error: {err}")


def _upload_file():
    """Upload a local .torrent file to Deluge."""
    from homelab.files import browse_local

    url = CFG.get("deluge_url", "")
    if not url:
        error("Deluge not configured.")
        return

    filepath = browse_local()
    if not filepath:
        return

    pw = CFG.get("deluge_password", "")
    auth = _rpc("auth.login", [pw])
    if not auth or not auth.get("result"):
        error("Deluge authentication failed.")
        return

    with open(filepath, "rb") as f:
        b64data = base64.b64encode(f.read()).decode()
    fname = os.path.basename(filepath)
    result = _rpc("core.add_torrent_file", [fname, b64data, {}])
    if result and result.get("result"):
        _set_label(result["result"])
        log_action("Deluge Upload", fname)
        success(f"Uploaded to Deluge: {fname}")
    else:
        error("Deluge upload failed.")
