"""Jellyfin media server plugin — library management, users, tasks, search."""

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


def _api(endpoint, method="GET", data=None):
    """Make an authenticated Jellyfin API call."""
    url = CFG.get("jellyfin_url", "").rstrip("/")
    token = CFG.get("jellyfin_token", "")
    if not url or not token:
        return None
    headers = {
        "X-Emby-Token": token,
        "Accept": "application/json",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{url}{endpoint}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            if raw:
                return json.loads(raw)
            return True
    except Exception:
        return None


def _api_post(endpoint):
    """Make an authenticated POST that may not return JSON."""
    url = CFG.get("jellyfin_url", "").rstrip("/")
    token = CFG.get("jellyfin_token", "")
    if not url or not token:
        return False
    req = urllib.request.Request(
        f"{url}{endpoint}",
        headers={"X-Emby-Token": token},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
        return True
    except Exception:
        return False


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
                    icon = f"{C.YELLOW}\u23f8{C.RESET}" if paused else f"{C.GREEN}\u25b6{C.RESET}"
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
            ("Jellyfin             \u2014 media, users, tasks, search", jellyfin_menu),
        ]

    def get_actions(self):
        return {
            "Jellyfin Now Playing": ("jellyfin_now_playing", _now_playing),
            "Jellyfin Library Stats": ("jellyfin_lib_stats", _library_stats),
            "Jellyfin Recently Added": ("jellyfin_recent", _recently_added),
            "Jellyfin Search": ("jellyfin_search", _search),
            "Jellyfin Users": ("jellyfin_users", _users),
            "Jellyfin Tasks": ("jellyfin_tasks", _scheduled_tasks),
            "Jellyfin Scan Libraries": ("jellyfin_scan", jellyfin_scan),
        }


def jellyfin_menu():
    while True:
        idx = pick_option("Jellyfin:", [
            "Now Playing          \u2014 active sessions and streams",
            "Library Stats        \u2014 item counts and storage",
            "Recently Added       \u2014 latest additions to library",
            "Search               \u2014 find media by title, year, genre",
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "Users                \u2014 manage users and permissions",
            "Scheduled Tasks      \u2014 view and trigger server tasks",
            "Activity Log         \u2014 recent server activity",
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "Scan Libraries       \u2014 trigger library refresh",
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "\u2605 Add to Favorites   \u2014 pin an action to the main menu",
            "\u2190 Back",
        ])
        if idx == 12:
            return
        elif idx in (4, 8, 10):
            continue
        elif idx == 11:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(JellyfinPlugin())
        elif idx == 0:
            _now_playing()
        elif idx == 1:
            _library_stats()
        elif idx == 2:
            _recently_added()
        elif idx == 3:
            _search()
        elif idx == 5:
            _users()
        elif idx == 6:
            _scheduled_tasks()
        elif idx == 7:
            _activity_log()
        elif idx == 9:
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

            icon = f"{C.YELLOW}\u23f8{C.RESET}" if paused else f"{C.GREEN}\u25b6{C.RESET}"
            choices.append(f"{icon} {user:<15} {title:<35} {pct_str:>4}  {tc_str}  ({device})")

        session_count = len(choices)
        choices.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        choices.append("\u21bb Refresh")
        choices.append("\u2190 Back")

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

    aidx = pick_option(f"{user} \u2014 {title}:", ["Stop stream", "\u2190 Back"])
    if aidx == 0:
        if not confirm(f"Stop stream for '{user}'?", default_yes=False):
            return
        if session_id:
            _api_post(f"/Sessions/{session_id}/Playing/Stop")
            success(f"Stream stopped: {user}")
            log_action("Jellyfin Stop Stream", user)
        else:
            error("Could not determine session ID.")


def _library_stats():
    """Show library item counts."""
    counts = _api("/Items/Counts")
    folders = _api("/Library/VirtualFolders")

    if not counts and not folders:
        error("Could not fetch library stats.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
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
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    if not data:
        warn("No recently added items.")
        return

    rows = []
    for item in data[:25]:
        name = item.get("Name", "?")
        series = item.get("SeriesName", "")
        if series:
            display = f"{series} \u2014 {name}"
        else:
            display = name

        itype = item.get("Type", "?")
        year = item.get("ProductionYear", "")
        year_str = f" ({year})" if year else ""

        rows.append(f"{display}{year_str}  [{itype}]")

    scrollable_list(f"Recently Added ({len(rows)}):", rows)


def _search():
    """Search Jellyfin library by title."""
    term = prompt_text("Search Jellyfin:")
    if not term:
        return

    encoded = urllib.parse.quote(term)
    data = _api(f"/Search/Hints?SearchTerm={encoded}&Limit=25")
    if not data:
        error("Search failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    hints = data.get("SearchHints", [])
    if not hints:
        info(f"No results for '{term}'.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        for h in hints:
            name = h.get("Name", "?")
            htype = h.get("Type", "?")
            year = h.get("ProductionYear", "")
            series = h.get("Series", "")

            type_label = {
                "Movie": "Movie", "Series": "Series", "Episode": "Episode",
                "Audio": "Song", "MusicAlbum": "Album", "MusicArtist": "Artist",
                "Person": "Person", "BoxSet": "Collection",
            }.get(htype, htype)

            if series:
                display = f"{series} \u2014 {name}"
            else:
                display = name
            year_str = f" ({year})" if year else ""
            choices.append(f"[{type_label:<10}] {display}{year_str}")

        choices.append("\u2190 Back")
        idx = pick_option(f"Search: '{term}' ({len(hints)} results):", choices)
        if idx >= len(hints):
            return
        _item_detail(hints[idx])


def _item_detail(item):
    """Show detail for a search result item."""
    item_id = item.get("ItemId", item.get("Id", ""))
    if not item_id:
        return

    detail = _api(f"/Items/{item_id}")
    if not detail:
        error("Could not fetch item details.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    name = detail.get("Name", "?")
    itype = detail.get("Type", "?")
    year = detail.get("ProductionYear", "")
    genres = ", ".join(detail.get("Genres", []))
    rating = detail.get("CommunityRating", "")
    overview = detail.get("Overview", "")
    runtime = detail.get("RunTimeTicks", 0)
    series = detail.get("SeriesName", "")

    print(f"\n  {C.BOLD}{name}{C.RESET}")
    if series:
        print(f"  {C.BOLD}Series:{C.RESET}   {series}")
    print(f"  {C.BOLD}Type:{C.RESET}     {itype}")
    if year:
        print(f"  {C.BOLD}Year:{C.RESET}     {year}")
    if genres:
        print(f"  {C.BOLD}Genres:{C.RESET}   {genres}")
    if rating:
        print(f"  {C.BOLD}Rating:{C.RESET}   {rating}")
    if runtime:
        minutes = int(runtime) // 600000000
        print(f"  {C.BOLD}Runtime:{C.RESET}  {minutes} min")
    if overview:
        # Truncate long overviews
        display_overview = overview[:300] + "..." if len(overview) > 300 else overview
        print(f"\n  {C.DIM}{display_overview}{C.RESET}")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _users():
    """Manage Jellyfin users."""
    while True:
        data = _api("/Users")
        if not data or not isinstance(data, list):
            error("Could not fetch users.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for user in data:
            name = user.get("Name", "?")
            is_admin = user.get("Policy", {}).get("IsAdministrator", False)
            is_disabled = user.get("Policy", {}).get("IsDisabled", False)

            if is_disabled:
                icon = f"{C.RED}\u25cf{C.RESET}"
                status = "Disabled"
            elif is_admin:
                icon = f"{C.GREEN}\u25cf{C.RESET}"
                status = "Admin"
            else:
                icon = f"{C.CYAN}\u25cf{C.RESET}"
                status = "User"

            last_active = user.get("LastActivityDate", "")
            if last_active:
                try:
                    dt = datetime.datetime.fromisoformat(last_active.replace("Z", "+00:00"))
                    age = time.time() - dt.timestamp()
                    if age < 3600:
                        active_str = f"{int(age / 60)}m ago"
                    elif age < 86400:
                        active_str = f"{int(age / 3600)}h ago"
                    else:
                        active_str = f"{int(age / 86400)}d ago"
                except (ValueError, OSError):
                    active_str = "?"
            else:
                active_str = "never"

            choices.append(f"{icon} {name:<20} ({status:<8})  last active: {active_str}")

        user_count = len(choices)
        choices.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        choices.append("\u21bb Refresh")
        choices.append("\u2190 Back")

        idx = pick_option(f"Users ({user_count}):", choices)
        if idx == user_count + 2:
            return
        elif idx in (user_count, user_count + 1):
            continue
        elif idx < user_count:
            _user_actions(data[idx])


def _user_actions(user):
    """Actions for a single Jellyfin user."""
    name = user.get("Name", "?")
    user_id = user.get("Id", "")
    policy = user.get("Policy", {})
    is_admin = policy.get("IsAdministrator", False)
    is_disabled = policy.get("IsDisabled", False)

    print(f"\n  {C.BOLD}{name}{C.RESET}")
    print(f"  {C.BOLD}Admin:{C.RESET}      {'Yes' if is_admin else 'No'}")
    print(f"  {C.BOLD}Status:{C.RESET}     {'Disabled' if is_disabled else 'Active'}")
    print(f"  {C.BOLD}Max Streams:{C.RESET} {policy.get('MaxActiveSessions', 'unlimited')}")

    last_active = user.get("LastActivityDate", "")
    if last_active:
        try:
            dt = datetime.datetime.fromisoformat(last_active.replace("Z", "+00:00"))
            print(f"  {C.BOLD}Last Active:{C.RESET} {dt.strftime('%Y-%m-%d %H:%M')}")
        except ValueError:
            pass

    toggle_label = "Enable" if is_disabled else "Disable"
    options = [
        f"{toggle_label} User       \u2014 {'enable' if is_disabled else 'disable'} this account",
        "Playback History     \u2014 recently watched items",
        "\u2190 Back",
    ]
    aidx = pick_option(f"User: {name}", options)
    if aidx == 2:
        return
    elif aidx == 0:
        new_disabled = not is_disabled
        action_word = "Disable" if new_disabled else "Enable"
        if confirm(f"{action_word} user '{name}'?", default_yes=False):
            policy["IsDisabled"] = new_disabled
            result = _api(f"/Users/{user_id}/Policy", method="POST", data=policy)
            if result is not None:
                success(f"User {action_word.lower()}d: {name}")
                log_action(f"Jellyfin {action_word} User", name)
            else:
                error(f"Failed to {action_word.lower()} user.")
    elif aidx == 1:
        _user_history(user_id, name)


def _user_history(user_id, username):
    """Show playback history for a user."""
    data = _api(f"/Items?UserId={user_id}&Filters=IsPlayed&SortBy=DatePlayed&SortOrder=Descending&Limit=30&Recursive=true")
    if not data:
        error("Could not fetch playback history.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    items = data.get("Items", [])
    if not items:
        info(f"No playback history for {username}.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    rows = []
    for item in items:
        name = item.get("Name", "?")
        series = item.get("SeriesName", "")
        itype = item.get("Type", "?")
        if series:
            display = f"{series} \u2014 {name}"
        else:
            display = name

        played = item.get("UserData", {}).get("LastPlayedDate", "")
        if played:
            try:
                dt = datetime.datetime.fromisoformat(played.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d")
            except ValueError:
                date_str = "?"
        else:
            date_str = "?"

        rows.append(f"{date_str}  {display}  [{itype}]")

    scrollable_list(f"History: {username} ({len(rows)}):", rows)


def _scheduled_tasks():
    """View and trigger Jellyfin scheduled tasks."""
    while True:
        data = _api("/ScheduledTasks")
        if not data or not isinstance(data, list):
            error("Could not fetch scheduled tasks.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for task in data:
            name = task.get("Name", "?")
            state = task.get("State", "Idle")
            last_result = task.get("LastExecutionResult", {})
            last_status = last_result.get("Status", "")
            last_end = last_result.get("EndTimeUtc", "")

            if state == "Running":
                pct = task.get("CurrentProgressPercentage", 0)
                icon = f"{C.CYAN}\u25cf{C.RESET}"
                status_str = f"running ({pct:.0f}%)"
            elif last_status == "Completed":
                icon = f"{C.GREEN}\u25cf{C.RESET}"
                status_str = "completed"
            elif last_status == "Failed":
                icon = f"{C.RED}\u25cf{C.RESET}"
                status_str = "failed"
            else:
                icon = f"{C.DIM}\u25cf{C.RESET}"
                status_str = "idle"

            # Last run time
            time_str = ""
            if last_end:
                try:
                    dt = datetime.datetime.fromisoformat(last_end.replace("Z", "+00:00"))
                    age = time.time() - dt.timestamp()
                    if age < 3600:
                        time_str = f"{int(age / 60)}m ago"
                    elif age < 86400:
                        time_str = f"{int(age / 3600)}h ago"
                    else:
                        time_str = f"{int(age / 86400)}d ago"
                except (ValueError, OSError):
                    pass

            display_name = name[:35] if len(name) > 35 else name
            last_str = f"  last: {time_str}" if time_str else ""
            choices.append(f"{icon} {display_name:<35} {status_str:<15}{last_str}")

        task_count = len(choices)
        choices.append("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        choices.append("\u21bb Refresh")
        choices.append("\u2190 Back")

        idx = pick_option(f"Scheduled Tasks ({task_count}):", choices)
        if idx == task_count + 2:
            return
        elif idx in (task_count, task_count + 1):
            continue
        elif idx < task_count:
            _task_actions(data[idx])


def _task_actions(task):
    """Actions for a single scheduled task."""
    name = task.get("Name", "?")
    task_id = task.get("Id", "")
    state = task.get("State", "Idle")
    description = task.get("Description", "")
    category = task.get("Category", "")
    last_result = task.get("LastExecutionResult", {})

    print(f"\n  {C.BOLD}{name}{C.RESET}")
    if description:
        print(f"  {C.DIM}{description}{C.RESET}")
    if category:
        print(f"  {C.BOLD}Category:{C.RESET} {category}")
    print(f"  {C.BOLD}State:{C.RESET}    {state}")

    if last_result:
        last_status = last_result.get("Status", "?")
        last_start = last_result.get("StartTimeUtc", "")
        last_end = last_result.get("EndTimeUtc", "")
        print(f"  {C.BOLD}Last Run:{C.RESET} {last_status}")
        if last_start and last_end:
            try:
                start_dt = datetime.datetime.fromisoformat(last_start.replace("Z", "+00:00"))
                end_dt = datetime.datetime.fromisoformat(last_end.replace("Z", "+00:00"))
                duration = (end_dt - start_dt).total_seconds()
                print(f"  {C.BOLD}Duration:{C.RESET} {int(duration)}s")
                print(f"  {C.BOLD}Finished:{C.RESET} {end_dt.strftime('%Y-%m-%d %H:%M')}")
            except ValueError:
                pass

    triggers = task.get("Triggers", [])
    if triggers:
        print(f"  {C.BOLD}Triggers:{C.RESET} {len(triggers)} configured")

    aidx = pick_option(f"Task: {name}", ["Run Now", "\u2190 Back"])
    if aidx == 0:
        if _api_post(f"/ScheduledTasks/Running/{task_id}"):
            success(f"Task triggered: {name}")
            log_action("Jellyfin Run Task", name)
        else:
            error(f"Failed to trigger task: {name}")


def _activity_log():
    """Show recent Jellyfin activity log."""
    data = _api("/System/ActivityLog/Entries?Limit=40")
    if not data:
        error("Could not fetch activity log.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    items = data.get("Items", [])
    if not items:
        info("No recent activity.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    rows = []
    for entry in items:
        name = entry.get("Name", "?")
        severity = entry.get("Severity", "Information")
        date_str = ""
        date_raw = entry.get("Date", "")
        if date_raw:
            try:
                dt = datetime.datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                date_str = "?"

        if severity == "Error":
            icon = f"{C.RED}\u2717{C.RESET}"
        elif severity == "Warning":
            icon = f"{C.YELLOW}\u25cf{C.RESET}"
        else:
            icon = f"{C.DIM}\u25cf{C.RESET}"

        short_text = entry.get("ShortOverview", "")
        detail = f"  {C.DIM}{short_text}{C.RESET}" if short_text else ""
        rows.append(f"{icon} {date_str}  {name}{detail}")

    scrollable_list(f"Activity Log ({len(rows)}):", rows)


def jellyfin_scan():
    """Trigger a Jellyfin library scan."""
    url = CFG.get("jellyfin_url", "").rstrip("/")
    token = CFG.get("jellyfin_token", "")
    if not url or not token:
        error("Jellyfin not configured. Set URL and token in Settings.")
        return

    info("Triggering Jellyfin library scan...")

    if _api_post("/Library/Refresh"):
        success("Jellyfin library scan triggered.")
        log_action("Jellyfin Scan", "All libraries")
        from homelab.notifications import notify
        notify("Homelab", "Jellyfin library scan triggered")
    else:
        error("Failed to trigger Jellyfin scan.")
