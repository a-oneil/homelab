"""Immich plugin — library stats, trigger jobs, view recent uploads."""

import json
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, info, success, error, warn
from homelab.modules.auditlog import log_action

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to Immich."""
    base = CFG.get("immich_url", "").rstrip("/")
    token = CFG.get("immich_api_key", "")
    if not base or not token:
        return None

    url = f"{base}/api{endpoint}"
    headers = {
        "x-api-key": token,
        "Content-Type": "application/json",
    }

    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"Immich API error: {e}")
        return None


class ImmichPlugin(Plugin):
    name = "Immich"
    key = "immich"

    def is_configured(self):
        return bool(CFG.get("immich_url") and CFG.get("immich_api_key"))

    def get_config_fields(self):
        return [
            ("immich_url", "Immich URL", "e.g. http://192.168.1.100:2283", False),
            ("immich_api_key", "Immich API Key", "from Account Settings", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        stats = _api("/server/statistics")
        if not stats:
            return []
        photos = stats.get("photos", 0)
        videos = stats.get("videos", 0)
        usage = stats.get("usage", 0)
        usage_gb = usage / (1024 ** 3) if usage else 0
        lines = [
            f"{photos:,} photos, {videos:,} videos",
            f"Storage: {usage_gb:.1f} GB",
        ]
        return [{"title": "Immich", "lines": lines}]

    def get_menu_items(self):
        return [
            ("Immich               — photos, library stats, jobs", immich_menu),
        ]

    def get_actions(self):
        return {
            "Immich Stats": ("immich_stats", _show_stats),
            "Immich Jobs": ("immich_jobs", _manage_jobs),
        }

    def resolve_favorite(self, fav):
        return None


def _fetch_stats():
    stats = _api("/server/statistics")
    if stats:
        photos = stats.get("photos", 0)
        videos = stats.get("videos", 0)
        usage = stats.get("usage", 0)
        usage_gb = usage / (1024 ** 3) if usage else 0
        _HEADER_CACHE["stats"] = f"Immich: {photos} photos, {videos} videos ({usage_gb:.1f}GB)"
    _HEADER_CACHE["timestamp"] = time.time()


def immich_menu():
    while True:
        idx = pick_option("Immich:", [
            "Albums               — browse albums",
            "Jobs                 — view and trigger background jobs",
            "Library Stats        — photo/video counts, storage usage",
            "Recent Uploads       — latest photos and videos",
            "Server Info          — version, features, storage",
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
            add_plugin_favorite(ImmichPlugin())
        elif idx == 0:
            _browse_albums()
        elif idx == 1:
            _manage_jobs()
        elif idx == 2:
            _show_stats()
        elif idx == 3:
            _recent_uploads()
        elif idx == 4:
            _server_info()


def _show_stats():
    """Show library statistics."""
    stats = _api("/server/statistics")
    if not stats:
        error("Could not fetch statistics.")
        return

    print(f"\n  {C.BOLD}Immich Library Statistics{C.RESET}\n")

    photos = stats.get("photos", 0)
    videos = stats.get("videos", 0)
    usage = stats.get("usage", 0)
    usage_gb = usage / (1024 ** 3) if usage else 0

    print(f"  {C.BOLD}Photos:{C.RESET}  {photos:,}")
    print(f"  {C.BOLD}Videos:{C.RESET}  {videos:,}")
    print(f"  {C.BOLD}Storage:{C.RESET} {usage_gb:.2f} GB")

    # Per-user stats if available
    users = stats.get("usageByUser", [])
    if users:
        print(f"\n  {C.BOLD}By User:{C.RESET}")
        for u in users:
            name = u.get("userName", "?")
            u_photos = u.get("photos", 0)
            u_videos = u.get("videos", 0)
            u_usage = u.get("usage", 0) / (1024 ** 3)
            print(f"    {name:<20} {u_photos:>6} photos  {u_videos:>4} videos  {u_usage:.1f}GB")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_info():
    """Show server version and features."""
    version = _api("/server/version")
    features = _api("/server/features")
    config = _api("/server/config")

    print(f"\n  {C.BOLD}Immich Server Info{C.RESET}\n")

    if version:
        print(f"  {C.BOLD}Version:{C.RESET} {version.get('major', '?')}.{version.get('minor', '?')}.{version.get('patch', '?')}")

    if config:
        print(f"  {C.BOLD}Login Page:{C.RESET} {config.get('loginPageMessage', 'default')}")

    if features:
        print(f"\n  {C.BOLD}Features:{C.RESET}")
        for key, val in features.items():
            icon = f"{C.GREEN}●{C.RESET}" if val else f"{C.DIM}○{C.RESET}"
            print(f"    {icon} {key}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _manage_jobs():
    """View and trigger Immich background jobs."""
    while True:
        jobs = _api("/jobs")
        if not jobs:
            error("Could not fetch jobs.")
            return

        choices = []
        job_names = []
        for name, data in jobs.items():
            active = data.get("queueStatus", {}).get("isActive", False)
            waiting = data.get("jobCounts", {}).get("waiting", 0)
            active_count = data.get("jobCounts", {}).get("active", 0)
            completed = data.get("jobCounts", {}).get("completed", 0)
            failed = data.get("jobCounts", {}).get("failed", 0)

            icon = f"{C.GREEN}●{C.RESET}" if active else f"{C.DIM}○{C.RESET}"
            status = f"active:{active_count} waiting:{waiting} done:{completed}"
            if failed:
                status += f" {C.RED}failed:{failed}{C.RESET}"
            choices.append(f"{icon} {name:<30} {status}")
            job_names.append(name)

        choices.append("← Back")
        idx = pick_option("Jobs:", choices)
        if idx >= len(job_names):
            return

        job_name = job_names[idx]
        _job_actions(job_name)


def _job_actions(job_name):
    """Actions for a specific job."""
    idx = pick_option(f"Job: {job_name}", [
        "Start job", "Pause job", "Resume all", "← Back",
    ])
    if idx == 3:
        return
    elif idx == 0:
        result = _api(f"/jobs/{job_name}", method="PUT", data={"command": "start"})
        if result:
            log_action("Immich Start Job", job_name)
            success(f"Started: {job_name}")
        else:
            error("Failed to start job.")
    elif idx == 1:
        result = _api(f"/jobs/{job_name}", method="PUT", data={"command": "pause"})
        if result:
            log_action("Immich Pause Job", job_name)
            success(f"Paused: {job_name}")
    elif idx == 2:
        result = _api(f"/jobs/{job_name}", method="PUT", data={"command": "resume"})
        if result:
            log_action("Immich Resume Job", job_name)
            success(f"Resumed: {job_name}")


def _recent_uploads():
    """Show recently uploaded assets."""
    # Use search to get recent assets
    result = _api("/search/metadata", method="POST", data={
        "order": "desc",
        "page": 1,
        "size": 20,
    })

    assets = []
    if result and result.get("assets"):
        assets = result["assets"].get("items", [])

    if not assets:
        # Fallback: try timeline
        info("No recent uploads found via search.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Recent Uploads{C.RESET}\n")
    for asset in assets:
        filename = asset.get("originalFileName", "?")
        atype = asset.get("type", "?")
        created = asset.get("createdAt", "?")[:10]
        size = asset.get("exifInfo", {}).get("fileSizeInByte", 0)
        size_mb = size / (1024 * 1024) if size else 0

        icon = "📷" if atype == "IMAGE" else "🎬"
        print(f"  {icon} {filename:<40} {created}  {size_mb:.1f}MB")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _browse_albums():
    """Browse Immich albums."""
    albums = _api("/albums")
    if not albums or not isinstance(albums, list):
        warn("No albums found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    choices = []
    for a in albums:
        name = a.get("albumName", "?")
        count = a.get("assetCount", 0)
        shared = f" {C.ACCENT}shared{C.RESET}" if a.get("shared") else ""
        choices.append(f"{name:<35} {count} assets{shared}")

    choices.append("← Back")
    idx = pick_option("Albums:", choices)
    if idx >= len(albums):
        return

    album = albums[idx]
    album_id = album.get("id")
    detail = _api(f"/albums/{album_id}")
    if detail:
        print(f"\n  {C.BOLD}{detail.get('albumName', '?')}{C.RESET}")
        print(f"  Assets: {detail.get('assetCount', 0)}")
        print(f"  Created: {detail.get('createdAt', '?')[:10]}")
        if detail.get("description"):
            print(f"  Description: {detail['description']}")
        owner = detail.get("owner", {})
        if owner:
            print(f"  Owner: {owner.get('name', '?')}")
    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
