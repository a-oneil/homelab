"""Plex media server plugin — library management, search, playlists, history."""

import datetime
import json
import time
import urllib.parse
import urllib.request

from homelab.modules.auditlog import log_action
from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, confirm, prompt_text, info, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint):
    """Make an authenticated Plex API call."""
    url = CFG.get("plex_url", "").rstrip("/")
    token = CFG.get("plex_token", "")
    if not url or not token:
        return None
    req = urllib.request.Request(
        f"{url}{endpoint}",
        headers={"X-Plex-Token": token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _api_cmd(endpoint, method="DELETE"):
    """Make an authenticated Plex request that may not return JSON."""
    url = CFG.get("plex_url", "").rstrip("/")
    token = CFG.get("plex_token", "")
    if not url or not token:
        return False
    req = urllib.request.Request(
        f"{url}{endpoint}",
        headers={"X-Plex-Token": token},
        method=method,
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
        return True
    except Exception:
        return False


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
            ("Plex                 — media, search, playlists, history", plex_menu),
        ]

    def get_actions(self):
        return {
            "Plex Now Playing": ("plex_now_playing", _now_playing),
            "Plex Library Stats": ("plex_lib_stats", _library_stats),
            "Plex Recently Added": ("plex_recent", _recently_added),
            "Plex Search": ("plex_search", _search),
            "Plex Library Browser": ("plex_browse", _library_browser),
            "Plex Playlists": ("plex_playlists", _playlists),
            "Plex Watch History": ("plex_history", _watch_history),
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
            "Search               — find media across all libraries",
            "───────────────",
            "Library Browser      — navigate sections and folders",
            "Playlists            — view and browse playlists",
            "Watch History        — playback history and stats",
            "───────────────",
            "Scan Libraries       — trigger library refresh",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 12:
            return
        elif idx in (4, 8, 10):
            continue
        elif idx == 11:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(PlexPlugin())
        elif idx == 0:
            _now_playing()
        elif idx == 1:
            _library_stats()
        elif idx == 2:
            _recently_added()
        elif idx == 3:
            _search()
        elif idx == 5:
            _library_browser()
        elif idx == 6:
            _playlists()
        elif idx == 7:
            _watch_history()
        elif idx == 9:
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
            log_action("Plex Kill Stream", user)
        elif session_key:
            _api(f"/video/:/transcode/universal/stop?session={session_key}")
            success(f"Transcode stopped: {user}")
            log_action("Plex Kill Stream", user)
        else:
            error("Could not determine session ID.")


def _library_stats():
    """Show library statistics with section counts."""
    data = _api("/library/sections")
    if not data:
        error("Could not fetch library sections.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    sections = data.get("MediaContainer", {}).get("Directory", [])
    if not sections:
        warn("No library sections found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
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
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    items = data.get("MediaContainer", {}).get("Metadata", [])
    if not items:
        warn("No recently added items.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
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


def _search():
    """Search across all Plex libraries."""
    term = prompt_text("Search Plex:")
    if not term:
        return

    encoded = urllib.parse.quote(term)
    data = _api(f"/search?query={encoded}")
    if not data:
        error("Search failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    items = data.get("MediaContainer", {}).get("Metadata", [])
    if not items:
        info(f"No results for '{term}'.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        for item in items[:30]:
            title = item.get("title", "?")
            itype = item.get("type", "?")
            year = item.get("year", "")
            parent = item.get("grandparentTitle", "")

            type_label = {
                "movie": "Movie", "show": "Show", "episode": "Episode",
                "track": "Song", "album": "Album", "artist": "Artist",
            }.get(itype, itype)

            if parent:
                display = f"{parent} — {title}"
            else:
                display = title
            year_str = f" ({year})" if year else ""
            choices.append(f"[{type_label:<8}] {display}{year_str}")

        choices.append("← Back")
        idx = pick_option(f"Search: '{term}' ({len(items)} results):", choices)
        if idx >= len(items) or idx == len(choices) - 1:
            return
        _plex_item_detail(items[idx])


def _plex_item_detail(item):
    """Show detail for a Plex item."""
    rating_key = item.get("ratingKey", "")
    if not rating_key:
        return

    detail = _api(f"/library/metadata/{rating_key}")
    if not detail:
        error("Could not fetch item details.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    metadata_list = detail.get("MediaContainer", {}).get("Metadata", [])
    if not metadata_list:
        return
    meta = metadata_list[0]

    title = meta.get("title", "?")
    itype = meta.get("type", "?")
    year = meta.get("year", "")
    summary = meta.get("summary", "")
    rating = meta.get("rating", "")
    audience_rating = meta.get("audienceRating", "")
    duration = meta.get("duration", 0)
    parent = meta.get("grandparentTitle", meta.get("parentTitle", ""))
    genres = [g.get("tag", "") for g in meta.get("Genre", [])]
    view_count = meta.get("viewCount", 0)

    print(f"\n  {C.BOLD}{title}{C.RESET}")
    if parent:
        print(f"  {C.BOLD}Series:{C.RESET}   {parent}")
    print(f"  {C.BOLD}Type:{C.RESET}     {itype}")
    if year:
        print(f"  {C.BOLD}Year:{C.RESET}     {year}")
    if genres:
        print(f"  {C.BOLD}Genres:{C.RESET}   {', '.join(genres)}")
    if rating:
        print(f"  {C.BOLD}Rating:{C.RESET}   {rating}")
    if audience_rating:
        print(f"  {C.BOLD}Audience:{C.RESET} {audience_rating}")
    if duration:
        minutes = int(duration) // 60000
        print(f"  {C.BOLD}Runtime:{C.RESET}  {minutes} min")
    if view_count:
        print(f"  {C.BOLD}Plays:{C.RESET}    {view_count}")
    if summary:
        display_summary = summary[:300] + "..." if len(summary) > 300 else summary
        print(f"\n  {C.DIM}{display_summary}{C.RESET}")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _library_browser():
    """Browse Plex library sections and their contents."""
    data = _api("/library/sections")
    if not data:
        error("Could not fetch library sections.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    sections = data.get("MediaContainer", {}).get("Directory", [])
    if not sections:
        warn("No library sections found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        for section in sections:
            title = section.get("title", "?")
            stype = section.get("type", "?")
            type_label = {"movie": "Movies", "show": "Shows", "artist": "Music", "photo": "Photos"}.get(stype, stype)
            choices.append(f"{title:<25} ({type_label})")

        choices.append("← Back")
        idx = pick_option("Library Browser:", choices)
        if idx >= len(sections):
            return
        _browse_section(sections[idx])


def _browse_section(section):
    """Browse items in a Plex library section."""
    key = section.get("key", "")
    title = section.get("title", "?")
    offset = 0
    page_size = 50

    while True:
        data = _api(f"/library/sections/{key}/all?X-Plex-Container-Start={offset}&X-Plex-Container-Size={page_size}")
        if not data:
            error("Could not fetch section items.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        mc = data.get("MediaContainer", {})
        items = mc.get("Metadata", [])
        total = mc.get("totalSize", mc.get("size", 0))

        if not items:
            info(f"No items in '{title}'.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for item in items:
            name = item.get("title", "?")
            year = item.get("year", "")
            itype = item.get("type", "?")
            year_str = f" ({year})" if year else ""

            view_count = item.get("viewCount", 0)
            view_str = f"  {C.DIM}{view_count} plays{C.RESET}" if view_count else ""

            choices.append(f"{name}{year_str}  [{itype}]{view_str}")

        item_count = len(choices)
        choices.append("───────────────")

        has_more = offset + page_size < total
        has_prev = offset > 0
        if has_prev:
            choices.append("← Previous page")
        if has_more:
            choices.append(f"→ Next page ({offset + page_size + 1}-{min(offset + 2 * page_size, total)} of {total})")

        choices.append("← Back")

        header = f"\n  {C.ACCENT}{C.BOLD}{title}{C.RESET}  {offset + 1}-{offset + len(items)} of {total}\n"
        idx = pick_option(f"{title}:", choices, header=header)

        sep_idx = item_count
        back_idx = len(choices) - 1

        if idx == back_idx:
            return
        elif idx == sep_idx:
            continue
        elif idx < item_count:
            _plex_item_detail(items[idx])
        else:
            # Navigation
            nav_label = choices[idx]
            if "Previous" in nav_label:
                offset = max(0, offset - page_size)
            elif "Next" in nav_label:
                offset += page_size


def _playlists():
    """Browse Plex playlists."""
    while True:
        data = _api("/playlists/all")
        if not data:
            error("Could not fetch playlists.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        items = data.get("MediaContainer", {}).get("Metadata", [])
        if not items:
            info("No playlists found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for pl in items:
            title = pl.get("title", "?")
            leaf_count = pl.get("leafCount", 0)
            duration = pl.get("duration", 0)
            pl_type = pl.get("playlistType", "?")

            dur_str = ""
            if duration:
                hours = int(duration) // 3600000
                mins = (int(duration) % 3600000) // 60000
                if hours > 0:
                    dur_str = f"{hours}h {mins}m"
                else:
                    dur_str = f"{mins}m"

            choices.append(f"{title:<30} {leaf_count:>4} items  {dur_str:>8}  [{pl_type}]")

        pl_count = len(choices)
        choices.append("───────────────")
        choices.append("↻ Refresh")
        choices.append("← Back")

        idx = pick_option(f"Playlists ({pl_count}):", choices)
        if idx == pl_count + 2:
            return
        elif idx in (pl_count, pl_count + 1):
            continue
        elif idx < pl_count:
            _playlist_items(items[idx])


def _playlist_items(playlist):
    """Show items in a Plex playlist."""
    rating_key = playlist.get("ratingKey", "")
    title = playlist.get("title", "?")
    if not rating_key:
        return

    data = _api(f"/playlists/{rating_key}/items?X-Plex-Container-Start=0&X-Plex-Container-Size=50")
    if not data:
        error("Could not fetch playlist items.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    items = data.get("MediaContainer", {}).get("Metadata", [])
    if not items:
        info(f"Playlist '{title}' is empty.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    rows = []
    for item in items:
        name = item.get("title", "?")
        parent = item.get("grandparentTitle", "")
        itype = item.get("type", "?")
        duration = item.get("duration", 0)

        if parent:
            display = f"{parent} — {name}"
        else:
            display = name

        dur_str = ""
        if duration:
            mins = int(duration) // 60000
            dur_str = f"  ({mins}m)"

        rows.append(f"{display}{dur_str}  [{itype}]")

    scrollable_list(f"{title} ({len(rows)} items):", rows)


def _watch_history():
    """Show Plex watch history."""
    data = _api("/status/sessions/history/all?sort=viewedAt:desc&X-Plex-Container-Start=0&X-Plex-Container-Size=40")
    if not data:
        error("Could not fetch watch history.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    mc = data.get("MediaContainer", {})
    items = mc.get("Metadata", [])

    if not items:
        info("No watch history found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Gather stats
    users = {}
    for item in items:
        account = item.get("accountID", 0)
        user_title = item.get("User", item.get("title", "?"))
        if isinstance(user_title, dict):
            user_title = user_title.get("title", "?")
        users[account] = users.get(account, 0) + 1

    total_plays = len(items)
    unique_users = len(users)
    header = (
        f"\n  {C.ACCENT}{C.BOLD}Watch History{C.RESET}"
        f"  {total_plays} plays  |  {unique_users} user(s)\n"
    )

    rows = []
    for item in items:
        title = item.get("title", "?")
        parent = item.get("grandparentTitle", "")
        itype = item.get("type", "?")
        viewed_at = item.get("viewedAt", 0)

        if parent:
            display = f"{parent} — {title}"
        else:
            display = title

        date_str = "?"
        if viewed_at:
            try:
                date_str = datetime.datetime.fromtimestamp(int(viewed_at)).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        type_label = {
            "movie": "movie", "episode": "episode", "track": "song",
        }.get(itype, itype)

        rows.append(f"{date_str}  {display}  [{type_label}]")

    scrollable_list("Watch History:", rows, header_line=header)


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
        if _api_cmd("/library/sections/all/refresh", method="GET"):
            success("Plex scan triggered for all libraries.")
            log_action("Plex Scan", "All libraries")
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
            _api_cmd(f"/library/sections/{key}/refresh", method="GET")
        success(f"Plex scan triggered for {len(directories)} libraries.")
        log_action("Plex Scan", "All libraries")
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
    _api_cmd(f"/library/sections/{key}/refresh", method="GET")
    success(f"Plex library scan triggered (section {key}).")
    log_action("Plex Scan", f"Section {key}")
    from homelab.notifications import notify
    notify("Homelab", "Plex library scan triggered")
