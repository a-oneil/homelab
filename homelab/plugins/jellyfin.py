"""Jellyfin media server plugin — library scanning, now playing, stats."""

import json
import time

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.transport import ssh_run
from homelab.ui import C, pick_option, scrollable_list, confirm, info, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET"):
    """Make an authenticated Jellyfin API call via SSH + curl."""
    url = CFG.get("jellyfin_url", "").rstrip("/")
    token = CFG.get("jellyfin_token", "")
    if not url or not token:
        return None
    method_flag = f"-X {method} " if method != "GET" else ""
    result = ssh_run(
        f"curl -s {method_flag}'{url}{endpoint}' "
        f"-H 'X-Emby-Token: {token}' "
        f"-H 'Accept: application/json'"
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _api_post(endpoint):
    """Make an authenticated POST that may not return JSON."""
    url = CFG.get("jellyfin_url", "").rstrip("/")
    token = CFG.get("jellyfin_token", "")
    if not url or not token:
        return False
    result = ssh_run(
        f"curl -s -X POST '{url}{endpoint}' "
        f"-H 'X-Emby-Token: {token}'"
    )
    return result.returncode == 0


def _fetch_jellyfin_stats():
    """Fetch stats for header display."""
    data = _api("/Sessions")
    if data and isinstance(data, list):
        playing = [s for s in data if s.get("NowPlayingItem")]
        if playing:
            _HEADER_CACHE["stats"] = f"Jellyfin: {len(playing)} stream(s)"
        else:
            _HEADER_CACHE["stats"] = "Jellyfin: idle"
    _HEADER_CACHE["timestamp"] = time.time()


class JellyfinPlugin(Plugin):
    name = "Jellyfin"
    key = "jellyfin"

    def is_configured(self):
        return bool(CFG.get("jellyfin_url") and CFG.get("jellyfin_token"))

    def get_config_fields(self):
        return [
            ("jellyfin_url", "Jellyfin URL", "e.g. http://192.168.1.100:8096", False),
            ("jellyfin_token", "Jellyfin Token", "API Key", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_jellyfin_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        counts = _api("/Items/Counts")
        sessions = _api("/Sessions")
        lines = []

        if counts and isinstance(counts, dict):
            parts = []
            movies = counts.get("MovieCount", 0)
            series = counts.get("SeriesCount", 0)
            episodes = counts.get("EpisodeCount", 0)
            if movies:
                parts.append(f"{movies} movies")
            if series:
                parts.append(f"{series} series")
            if episodes:
                parts.append(f"{episodes} episodes")
            if parts:
                lines.append(", ".join(parts))

        if sessions and isinstance(sessions, list):
            playing = [s for s in sessions if s.get("NowPlayingItem")]
            if playing:
                lines.append(f"{C.GREEN}{len(playing)} active stream(s){C.RESET}")
                for s in playing[:3]:
                    user = s.get("UserName", "?")
                    item = s.get("NowPlayingItem", {})
                    title = item.get("SeriesName", item.get("Name", "?"))
                    paused = s.get("PlayState", {}).get("IsPaused", False)
                    icon = f"{C.YELLOW}⏸{C.RESET}" if paused else f"{C.GREEN}▶{C.RESET}"
                    lines.append(f"  {icon} {user}: {title}")
            else:
                lines.append(f"{C.DIM}No active streams{C.RESET}")

        if not lines:
            return []
        return [{"title": "Jellyfin", "lines": lines}]

    def get_health_alerts(self):
        data = _api("/System/Info")
        if data is None:
            return [f"{C.RED}Jellyfin:{C.RESET} unreachable"]
        return []

    def get_menu_items(self):
        return [
            ("Jellyfin             — scan and manage media libraries", jellyfin_menu),
        ]

    def get_actions(self):
        return {
            "Jellyfin Now Playing": ("jellyfin_now_playing", _now_playing),
            "Jellyfin Library Stats": ("jellyfin_lib_stats", _library_stats),
            "Jellyfin Recently Added": ("jellyfin_recent", _recently_added),
            "Jellyfin Scan Libraries": ("jellyfin_scan", jellyfin_scan),
        }


def jellyfin_menu():
    while True:
        idx = pick_option("Jellyfin:", [
            "Now Playing          — active sessions and streams",
            "Library Stats        — item counts and storage",
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
            add_plugin_favorite(JellyfinPlugin())
        elif idx == 0:
            _now_playing()
        elif idx == 1:
            _library_stats()
        elif idx == 2:
            _recently_added()
        elif idx == 3:
            jellyfin_scan()


def _now_playing():
    """Show active Jellyfin sessions."""
    while True:
        data = _api("/Sessions")
        if not data or not isinstance(data, list):
            warn("Could not fetch sessions.")
            return

        playing = [s for s in data if s.get("NowPlayingItem")]
        if not playing:
            info("No active streams.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for s in playing:
            user = s.get("UserName", "?")
            item = s.get("NowPlayingItem", {})
            series = item.get("SeriesName", "")
            if series:
                season = item.get("ParentIndexNumber", 0)
                episode = item.get("IndexNumber", 0)
                ep_name = item.get("Name", "")
                title = f"{series} S{int(season):02d}E{int(episode):02d}"
                if ep_name:
                    title += f" {ep_name}"
            else:
                title = item.get("Name", "?")

            device = s.get("DeviceName", "?")
            play_state = s.get("PlayState", {})
            paused = play_state.get("IsPaused", False)

            # Transcode info
            transcode = s.get("TranscodingInfo", {})
            if transcode:
                tc_str = "transcode" if transcode.get("IsVideoDirect") is False else "direct"
            else:
                tc_str = "direct"

            # Progress
            ticks = int(item.get("RunTimeTicks", 0))
            pos_ticks = int(play_state.get("PositionTicks", 0))
            if ticks > 0:
                pct = (pos_ticks / ticks) * 100
                pct_str = f"{pct:.0f}%"
            else:
                pct_str = "?"

            icon = f"{C.YELLOW}⏸{C.RESET}" if paused else f"{C.GREEN}▶{C.RESET}"
            choices.append(f"{icon} {user:<15} {title:<35} {pct_str:>4}  {tc_str}  ({device})")

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
            _session_detail(playing[idx])


def _session_detail(session):
    """Show detail for an active session with option to stop."""
    user = session.get("UserName", "?")
    item = session.get("NowPlayingItem", {})
    title = item.get("SeriesName", item.get("Name", "?"))
    session_id = session.get("Id", "")
    play_state = session.get("PlayState", {})

    print(f"\n  {C.BOLD}Stream: {title}{C.RESET}")
    print(f"  {C.BOLD}User:{C.RESET}     {user}")
    print(f"  {C.BOLD}Client:{C.RESET}   {session.get('Client', '?')}")
    print(f"  {C.BOLD}Device:{C.RESET}   {session.get('DeviceName', '?')}")
    print(f"  {C.BOLD}State:{C.RESET}    {'paused' if play_state.get('IsPaused') else 'playing'}")

    transcode = session.get("TranscodingInfo", {})
    if transcode:
        print(f"  {C.BOLD}Video:{C.RESET}    {'direct' if transcode.get('IsVideoDirect') else 'transcode'}")
        print(f"  {C.BOLD}Audio:{C.RESET}    {'direct' if transcode.get('IsAudioDirect') else 'transcode'}")

    aidx = pick_option(f"{user} — {title}:", ["Stop stream", "← Back"])
    if aidx == 0:
        if not confirm(f"Stop stream for '{user}'?", default_yes=False):
            return
        if session_id:
            _api_post(f"/Sessions/{session_id}/Playing/Stop")
            success(f"Stream stopped: {user}")
        else:
            error("Could not determine session ID.")


def _library_stats():
    """Show library item counts."""
    counts = _api("/Items/Counts")
    folders = _api("/Library/VirtualFolders")

    if not counts and not folders:
        error("Could not fetch library stats.")
        return

    print(f"\n  {C.BOLD}Jellyfin Library Statistics{C.RESET}\n")

    if folders and isinstance(folders, list):
        for folder in folders:
            name = folder.get("Name", "?")
            ctype = folder.get("CollectionType", "mixed")
            type_label = {
                "movies": "Movies", "tvshows": "TV Shows", "music": "Music",
                "books": "Books", "homevideos": "Home Videos", "photos": "Photos",
            }.get(ctype, ctype)
            print(f"  {name:<25} ({type_label})")
        print()

    if counts and isinstance(counts, dict):
        stats = [
            ("Movies", counts.get("MovieCount", 0)),
            ("Series", counts.get("SeriesCount", 0)),
            ("Episodes", counts.get("EpisodeCount", 0)),
            ("Artists", counts.get("ArtistCount", 0)),
            ("Albums", counts.get("AlbumCount", 0)),
            ("Songs", counts.get("SongCount", 0)),
            ("Books", counts.get("BookCount", 0)),
        ]
        for label, count in stats:
            if count:
                print(f"  {label:<15} {count:>8,}")

        total = sum(c for _, c in stats)
        print(f"\n  {C.BOLD}Total:{C.RESET} {total:,} items")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _recently_added():
    """Show recently added media."""
    data = _api("/Items/Latest")
    if not data or not isinstance(data, list):
        error("Could not fetch recent additions.")
        return

    if not data:
        warn("No recently added items.")
        return

    rows = []
    for item in data[:25]:
        name = item.get("Name", "?")
        series = item.get("SeriesName", "")
        if series:
            display = f"{series} — {name}"
        else:
            display = name

        itype = item.get("Type", "?")
        year = item.get("ProductionYear", "")
        year_str = f" ({year})" if year else ""

        rows.append(f"{display}{year_str}  [{itype}]")

    scrollable_list(f"Recently Added ({len(rows)}):", rows)


def jellyfin_scan():
    """Trigger a Jellyfin library scan."""
    url = CFG.get("jellyfin_url", "").rstrip("/")
    token = CFG.get("jellyfin_token", "")
    if not url or not token:
        error("Jellyfin not configured. Set URL and token in Settings.")
        return

    info("Triggering Jellyfin library scan...")

    result = ssh_run(
        f"curl -s -X POST '{url}/Library/Refresh' "
        f"-H 'X-Emby-Token: {token}'"
    )
    if result.returncode == 0:
        success("Jellyfin library scan triggered.")
        from homelab.notifications import notify
        notify("Homelab", "Jellyfin library scan triggered")
    else:
        error("Failed to trigger Jellyfin scan.")
