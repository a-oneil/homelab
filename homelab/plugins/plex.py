"""Plex media server plugin — library scanning, now playing, stats."""

import datetime
import json
import time

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.transport import ssh_run
from homelab.ui import C, pick_option, scrollable_list, confirm, info, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint):
    """Make an authenticated Plex API call via SSH + curl."""
    url = CFG.get("plex_url", "").rstrip("/")
    token = CFG.get("plex_token", "")
    if not url or not token:
        return None
    result = ssh_run(
        f"curl -s '{url}{endpoint}' "
        f"-H 'X-Plex-Token: {token}' "
        f"-H 'Accept: application/json'"
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _api_cmd(endpoint, method="DELETE"):
    """Make an authenticated Plex request that may not return JSON."""
    url = CFG.get("plex_url", "").rstrip("/")
    token = CFG.get("plex_token", "")
    if not url or not token:
        return False
    result = ssh_run(
        f"curl -s -X {method} '{url}{endpoint}' "
        f"-H 'X-Plex-Token: {token}'"
    )
    return result.returncode == 0


def _fetch_plex_stats():
    """Fetch stats for header display."""
    data = _api("/status/sessions")
    if data:
        container = data.get("MediaContainer", {})
        size = container.get("size", 0)
        if size > 0:
            _HEADER_CACHE["stats"] = f"Plex: {size} stream(s)"
        else:
            _HEADER_CACHE["stats"] = "Plex: idle"
    _HEADER_CACHE["timestamp"] = time.time()


class PlexPlugin(Plugin):
    name = "Plex"
    key = "plex"

    def is_configured(self):
        return bool(CFG.get("plex_url") and CFG.get("plex_token"))

    def get_config_fields(self):
        return [
            ("plex_url", "Plex URL", "e.g. http://192.168.1.100:32400", False),
            ("plex_token", "Plex Token", "X-Plex-Token", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_plex_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        data = _api("/status/sessions")
        lib_data = _api("/library/sections")
        lines = []

        if lib_data:
            sections = lib_data.get("MediaContainer", {}).get("Directory", [])
            if sections:
                lines.append(f"{len(sections)} libraries")

        if data:
            container = data.get("MediaContainer", {})
            size = container.get("size", 0)
            if size > 0:
                sessions = container.get("Metadata", [])
                lines.append(f"{C.GREEN}{size} active stream(s){C.RESET}")
                for s in sessions[:3]:
                    user = s.get("User", {}).get("title", "?")
                    title = s.get("grandparentTitle", s.get("title", "?"))
                    state = s.get("Player", {}).get("state", "?")
                    icon = f"{C.GREEN}▶{C.RESET}" if state == "playing" else f"{C.YELLOW}⏸{C.RESET}"
                    lines.append(f"  {icon} {user}: {title}")
            else:
                lines.append(f"{C.DIM}No active streams{C.RESET}")

        if not lines:
            return []
        return [{"title": "Plex", "lines": lines}]

    def get_health_alerts(self):
        data = _api("/")
        if data is None:
            return [f"{C.RED}Plex:{C.RESET} unreachable"]
        return []

    def get_menu_items(self):
        return [
            ("Plex                 — scan and manage media libraries", plex_menu),
        ]

    def get_actions(self):
        return {
            "Plex Now Playing": ("plex_now_playing", _now_playing),
            "Plex Library Stats": ("plex_lib_stats", _library_stats),
            "Plex Recently Added": ("plex_recent", _recently_added),
            "Plex Scan Libraries": ("plex_scan", plex_scan),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "plex_library":
            key = fav["id"]
            return lambda k=key: _scan_library(k)


def plex_menu():
    while True:
        idx = pick_option("Plex:", [
            "Now Playing          — active streams and sessions",
            "Library Stats        — section counts and sizes",
            "Recently Added       — latest additions to library",
            "Scan Libraries       — trigger library refresh",
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
            add_plugin_favorite(PlexPlugin())
        elif idx == 0:
            _now_playing()
        elif idx == 1:
            _library_stats()
        elif idx == 2:
            _recently_added()
        elif idx == 3:
            plex_scan()


def _now_playing():
    """Show active Plex streams."""
    while True:
        data = _api("/status/sessions")
        if not data:
            warn("Could not fetch active sessions.")
            return

        container = data.get("MediaContainer", {})
        sessions = container.get("Metadata", [])
        size = container.get("size", 0)

        if not sessions or size == 0:
            info("No active streams.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for s in sessions:
            user = s.get("User", {}).get("title", "?")
            title = s.get("grandparentTitle", "")
            if title:
                season = s.get("parentIndex", 0)
                episode = s.get("index", 0)
                ep_title = s.get("title", "")
                title = f"{title} S{int(season):02d}E{int(episode):02d}"
                if ep_title:
                    title += f" {ep_title}"
            else:
                title = s.get("title", "?")

            player = s.get("Player", {}).get("product", "?")
            state = s.get("Player", {}).get("state", "?")

            transcode = s.get("TranscodeSession", {})
            if transcode:
                decision = transcode.get("videoDecision", "direct")
                tc_str = "transcode" if decision == "transcode" else "direct"
            else:
                tc_str = "direct"

            duration = int(s.get("duration", 0))
            view_offset = int(s.get("viewOffset", 0))
            if duration > 0:
                pct = (view_offset / duration) * 100
                pct_str = f"{pct:.0f}%"
            else:
                pct_str = "?"

            icon = f"{C.GREEN}▶{C.RESET}" if state == "playing" else f"{C.YELLOW}⏸{C.RESET}"
            choices.append(f"{icon} {user:<15} {title:<35} {pct_str:>4}  {tc_str}  ({player})")

        session_count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Now Playing ({session_count}):", choices)
        if idx == session_count or idx == session_count + 1:
            continue
        elif idx == session_count + 2:
            return
        else:
            _session_detail(sessions[idx])


def _session_detail(session):
    """Show detail for an active session with option to kill."""
    user = session.get("User", {}).get("title", "?")
    title = session.get("grandparentTitle", session.get("title", "?"))
    session_id = session.get("Session", {}).get("id", "")
    session_key = session.get("sessionKey", "")
    player = session.get("Player", {})

    print(f"\n  {C.BOLD}Stream: {title}{C.RESET}")
    print(f"  {C.BOLD}User:{C.RESET}     {user}")
    print(f"  {C.BOLD}Player:{C.RESET}   {player.get('product', '?')} ({player.get('platform', '?')})")
    print(f"  {C.BOLD}State:{C.RESET}    {player.get('state', '?')}")
    print(f"  {C.BOLD}Address:{C.RESET}  {player.get('address', '?')}")

    transcode = session.get("TranscodeSession", {})
    if transcode:
        print(f"  {C.BOLD}Video:{C.RESET}    {transcode.get('videoDecision', '?')}")
        print(f"  {C.BOLD}Audio:{C.RESET}    {transcode.get('audioDecision', '?')}")

    aidx = pick_option(f"{user} — {title}:", ["Kill stream", "← Back"])
    if aidx == 0:
        if not confirm(f"Kill stream for '{user}'?", default_yes=False):
            return
        if session_id:
            _api_cmd(f"/status/sessions/terminate?sessionId={session_id}&reason=Terminated+by+admin")
            success(f"Stream terminated: {user}")
        elif session_key:
            _api(f"/video/:/transcode/universal/stop?session={session_key}")
            success(f"Transcode stopped: {user}")
        else:
            error("Could not determine session ID.")


def _library_stats():
    """Show library statistics with section counts."""
    data = _api("/library/sections")
    if not data:
        error("Could not fetch library sections.")
        return

    sections = data.get("MediaContainer", {}).get("Directory", [])
    if not sections:
        warn("No library sections found.")
        return

    print(f"\n  {C.BOLD}Plex Library Statistics{C.RESET}\n")

    total_items = 0
    for section in sections:
        title = section.get("title", "?")
        stype = section.get("type", "?")
        key = section.get("key", "")

        count_data = _api(f"/library/sections/{key}/all?X-Plex-Container-Size=0&X-Plex-Container-Start=0")
        count = 0
        if count_data:
            mc = count_data.get("MediaContainer", {})
            count = mc.get("totalSize", mc.get("size", 0))

        total_items += count

        type_label = {"movie": "Movies", "show": "Shows", "artist": "Music"}.get(stype, stype)
        print(f"  {title:<25} {count:>6} items  ({type_label})")

    print(f"\n  {C.BOLD}Total:{C.RESET} {total_items:,} items across {len(sections)} libraries")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _recently_added():
    """Show recently added media."""
    data = _api("/library/recentlyAdded?X-Plex-Container-Start=0&X-Plex-Container-Size=25")
    if not data:
        error("Could not fetch recent additions.")
        return

    items = data.get("MediaContainer", {}).get("Metadata", [])
    if not items:
        warn("No recently added items.")
        return

    rows = []
    for item in items:
        title = item.get("title", "?")
        parent = item.get("grandparentTitle", item.get("parentTitle", ""))
        if parent:
            display = f"{parent} — {title}"
        else:
            display = title

        itype = item.get("type", "?")
        added = item.get("addedAt", 0)
        if added:
            added_str = datetime.datetime.fromtimestamp(int(added)).strftime("%Y-%m-%d")
        else:
            added_str = "?"

        year = item.get("year", "")
        year_str = f" ({year})" if year else ""

        rows.append(f"{added_str}  {display}{year_str}  [{itype}]")

    scrollable_list(f"Recently Added ({len(rows)}):", rows)


def plex_scan():
    """Trigger a Plex library scan."""
    url = CFG.get("plex_url", "").rstrip("/")
    token = CFG.get("plex_token", "")
    if not url or not token:
        error("Plex not configured. Set URL and token in Settings.")
        return

    info("Fetching Plex libraries...")

    data = _api("/library/sections")
    if not data:
        error("Failed to connect to Plex.")
        return

    directories = data.get("MediaContainer", {}).get("Directory", [])
    if not directories:
        # Fallback: scan all
        result = ssh_run(
            f"curl -s -X GET '{url}/library/sections/all/refresh' "
            f"-H 'X-Plex-Token: {token}'"
        )
        if result.returncode == 0:
            success("Plex scan triggered for all libraries.")
        else:
            error("Failed to trigger Plex scan.")
        return

    choices = ["Scan ALL libraries"]
    for d in directories:
        choices.append(f"{d.get('title', '?')} ({d.get('type', '?')})")
    choices.append("Cancel")

    idx = pick_option("Select library to scan:", choices)
    if idx == len(choices) - 1:
        return

    if idx == 0:
        for d in directories:
            key = d.get("key", "")
            ssh_run(
                f"curl -s -X GET '{url}/library/sections/{key}/refresh' "
                f"-H 'X-Plex-Token: {token}'"
            )
        success(f"Plex scan triggered for {len(directories)} libraries.")
        from homelab.notifications import notify
        notify("Homelab", "Plex library scan triggered")
    else:
        d = directories[idx - 1]
        title = d.get("title", "?")
        key = d.get("key", "")

        action_choices = ["Scan", "★ Favorite", "← Back"]
        aidx = pick_option(f"Library: {title}", action_choices)
        if action_choices[aidx] == "← Back":
            return
        elif action_choices[aidx] == "★ Favorite":
            from homelab.plugins import add_item_favorite
            add_item_favorite("plex_library", key, f"Plex: Scan {title}")
        elif action_choices[aidx] == "Scan":
            _scan_library(key)


def _scan_library(key):
    """Scan a single Plex library by section key."""
    url = CFG.get("plex_url", "").rstrip("/")
    token = CFG.get("plex_token", "")
    if not url or not token:
        error("Plex not configured.")
        return
    ssh_run(
        f"curl -s -X GET '{url}/library/sections/{key}/refresh' "
        f"-H 'X-Plex-Token: {token}'"
    )
    success(f"Plex library scan triggered (section {key}).")
    from homelab.notifications import notify
    notify("Homelab", "Plex library scan triggered")
