"""Shared base for *arr plugins (Sonarr, Radarr, Lidarr).

All three share the same API pattern: X-Api-Key auth, /api/v{N}/ endpoints,
similar queue/calendar/health/system/status structures.
"""

import json
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin, add_plugin_favorite
from homelab.ui import C, pick_option, confirm, prompt_text, success, error, warn


def arr_api(base_url, api_key, api_version, endpoint, method="GET", data=None):
    """Make an authenticated API call to an *arr service."""
    if not base_url or not api_key:
        return None

    url = f"{base_url.rstrip('/')}/api/{api_version}{endpoint}"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }

    payload = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except Exception as e:
        error(f"API error: {e}")
        return None


class ArrPlugin(Plugin):
    """Base class for Sonarr/Radarr/Lidarr plugins.

    Subclasses must set these class attributes:
        _service        - config prefix, e.g. "sonarr"
        _display        - display name, e.g. "Sonarr"
        _api_version    - "v3" (sonarr/radarr) or "v1" (lidarr)
        _media_endpoint - "/series", "/movie", "/artist"
        _media_label    - "series", "movies", "artists"
        _media_singular - "series", "movie", "artist"
    """

    _service = ""
    _display = ""
    _api_version = "v3"
    _media_endpoint = ""
    _media_label = ""
    _media_singular = ""

    def __init__(self):
        self.name = self._display
        self.key = self._service
        self._cache = {"timestamp": 0, "stats": "", "alerts": []}
        self._cache_ttl = 300

    @property
    def _url(self):
        return CFG.get(f"{self._service}_url", "")

    @property
    def _api_key(self):
        return CFG.get(f"{self._service}_api_key", "")

    def _api(self, endpoint, method="GET", data=None):
        return arr_api(self._url, self._api_key, self._api_version, endpoint, method, data)

    def is_configured(self):
        return bool(self._url and self._api_key)

    def get_config_fields(self):
        return [
            (f"{self._service}_url", f"{self._display} URL", "e.g. http://192.168.1.100:8989", False),
            (f"{self._service}_api_key", f"{self._display} API Key", "from Settings → General", True),
        ]

    def get_health_alerts(self):
        if time.time() - self._cache["timestamp"] > self._cache_ttl:
            self._refresh_cache()
        return list(self._cache.get("alerts", []))

    def get_header_stats(self):
        if time.time() - self._cache["timestamp"] > self._cache_ttl:
            self._refresh_cache()
        return self._cache.get("stats") or None

    def _refresh_cache(self):
        self._cache["timestamp"] = time.time()
        # Stats
        media = self._api(self._media_endpoint)
        queue = self._api("/queue?page=1&pageSize=1")
        if media and isinstance(media, list):
            total = len(media)
            q_count = queue.get("totalRecords", 0) if queue and isinstance(queue, dict) else 0
            q_part = f" ({q_count} queued)" if q_count else ""
            self._cache["stats"] = f"{self._display}: {total} {self._media_label}{q_part}"

        # Health alerts
        health = self._api("/health")
        if health and isinstance(health, list):
            warnings = [h for h in health if h.get("type", "").lower() in ("warning", "error")]
            if warnings:
                msgs = [h.get("message", "?")[:50] for h in warnings[:2]]
                if len(warnings) > 2:
                    msgs.append(f"+{len(warnings) - 2} more")
                self._cache["alerts"] = [f"{C.YELLOW}{self._display}:{C.RESET} {'; '.join(msgs)}"]
            else:
                self._cache["alerts"] = []
        else:
            self._cache["alerts"] = []

    def get_dashboard_widgets(self):
        media = self._api(self._media_endpoint)
        queue = self._api("/queue?page=1&pageSize=5")
        lines = []
        if media and isinstance(media, list):
            monitored = sum(1 for m in media if m.get("monitored"))
            lines.append(f"{len(media)} {self._media_label} ({monitored} monitored)")
        q_records = queue.get("records", []) if queue and isinstance(queue, dict) else []
        q_total = queue.get("totalRecords", 0) if queue and isinstance(queue, dict) else 0
        if q_total:
            lines.append(f"{q_total} in queue:")
            for r in q_records[:3]:
                title = r.get("title", "?")[:45]
                status = r.get("status", "?")
                size = r.get("size", 0)
                left = r.get("sizeleft", 0)
                pct = f"{((size - left) / size * 100):.0f}%" if size > 0 else "?"
                icon = f"{C.GREEN}↓{C.RESET}" if status == "downloading" else f"{C.DIM}…{C.RESET}"
                lines.append(f"  {icon} {title}  {pct}")
        if not lines:
            return []
        return [{"title": self._display, "lines": lines}]

    def get_menu_items(self):
        desc = self._menu_description()
        pad = max(1, 22 - len(self._display))
        return [
            (f"{self._display}{' ' * pad}— {desc}", lambda: self._main_menu()),
        ]

    def _menu_description(self):
        return f"{self._media_label}, queue, calendar"

    def get_actions(self):
        return {
            f"{self._display} Library": (f"{self._service}_library", lambda: self._list_media()),
            f"{self._display} Queue": (f"{self._service}_queue", lambda: self._view_queue()),
        }

    def resolve_favorite(self, fav):
        return None

    # ── Menu ──────────────────────────────────────────────────────────────

    def _main_menu(self):
        while True:
            idx = pick_option(f"{self._display}:", [
                f"Library              — browse {self._media_label}",
                f"Search & Add         — find new {self._media_label} to monitor",
                "Queue                — active downloads",
                "Calendar             — upcoming releases",
                "Activity             — recent history",
                "System Status        — version, health, disk",
                "───────────────",
                "★ Add to Favorites   — pin an action to the main menu",
                "← Back",
            ])
            if idx == 8:
                return
            elif idx == 6:
                continue
            elif idx == 7:
                add_plugin_favorite(self)
            elif idx == 0:
                self._list_media()
            elif idx == 1:
                self._search_and_add()
            elif idx == 2:
                self._view_queue()
            elif idx == 3:
                self._view_calendar()
            elif idx == 4:
                self._view_activity()
            elif idx == 5:
                self._system_status()

    # ── Library ───────────────────────────────────────────────────────────

    def _list_media(self):
        while True:
            items = self._api(self._media_endpoint)
            if not items or not isinstance(items, list):
                warn(f"No {self._media_label} found.")
                return

            choices = []
            for item in items:
                choices.append(self._format_media_line(item))

            choices.append("← Back")
            idx = pick_option(f"{self._display} — {self._media_label.title()}:", choices)
            if idx >= len(items):
                return

            self._media_detail(items[idx])

    def _format_media_line(self, item):
        """Override in subclass for custom display."""
        title = item.get("title", item.get("artistName", "?"))
        monitored = f"{C.GREEN}●{C.RESET}" if item.get("monitored") else f"{C.DIM}○{C.RESET}"
        return f"{monitored} {title}"

    def _media_detail(self, item):
        """Override in subclass for custom detail view."""
        title = item.get("title", item.get("artistName", "?"))
        print(f"\n  {C.BOLD}{title}{C.RESET}")
        self._print_media_details(item)

        monitored = item.get("monitored", False)
        toggle = "Unmonitor" if monitored else "Monitor"
        item_id = item.get("id")

        choices = [toggle, "← Back"]
        aidx = pick_option(f"{title}:", choices)

        if aidx == 0 and item_id:
            item["monitored"] = not monitored
            self._api(f"{self._media_endpoint}/{item_id}", method="PUT", data=item)
            success(f"{'Unmonitored' if monitored else 'Monitored'}: {title}")

    def _print_media_details(self, item):
        """Override in subclass."""
        pass

    # ── Queue ─────────────────────────────────────────────────────────────

    def _view_queue(self):
        while True:
            queue = self._api("/queue?page=1&pageSize=50&includeUnknownSeriesItems=true")
            if not queue or not queue.get("records"):
                warn("Queue is empty.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
                return

            records = queue["records"]
            print(f"\n  {C.BOLD}{self._display} Queue{C.RESET} ({queue.get('totalRecords', 0)} items)\n")

            choices = []
            for r in records:
                title = r.get("title", "?")
                status = r.get("status", "?")
                progress = r.get("sizeleft", 0)
                size = r.get("size", 0)
                if size > 0:
                    pct = ((size - progress) / size) * 100
                    pct_str = f"{pct:.0f}%"
                else:
                    pct_str = "?"

                if status == "downloading":
                    icon = f"{C.GREEN}↓{C.RESET}"
                elif status == "completed":
                    icon = f"{C.GREEN}✓{C.RESET}"
                else:
                    icon = f"{C.DIM}…{C.RESET}"

                choices.append(f"{icon} {title:<50} {pct_str:>4}  [{status}]")

            choices.append("← Back")
            idx = pick_option(f"{self._display} Queue:", choices)
            if idx >= len(records):
                return

            self._queue_item_actions(records[idx])

    def _queue_item_actions(self, record):
        """Actions for a queue item."""
        title = record.get("title", "?")
        record_id = record.get("id")

        print(f"\n  {C.BOLD}{title}{C.RESET}")
        print(f"  Status: {record.get('status', '?')}")
        print(f"  Protocol: {record.get('protocol', '?')}")
        dl = record.get("downloadClient", "?")
        if dl:
            print(f"  Client: {dl}")

        tracked = record.get("statusMessages", [])
        if tracked:
            for msg in tracked:
                t = msg.get("title", "")
                msgs = msg.get("messages", [])
                if t:
                    print(f"  {C.YELLOW}{t}{C.RESET}")
                for m in msgs:
                    print(f"    {m}")

        choices = ["Remove from queue", "← Back"]
        aidx = pick_option(f"{title}:", choices)
        if aidx == 0 and record_id:
            if confirm(f"Remove '{title}' from queue?", default_yes=False):
                self._api(f"/queue/{record_id}?removeFromClient=false&blocklist=false", method="DELETE")
                success(f"Removed: {title}")

    # ── Calendar ──────────────────────────────────────────────────────────

    def _view_calendar(self):
        import datetime
        today = datetime.date.today()
        start = today.isoformat()
        end = (today + datetime.timedelta(days=14)).isoformat()

        cal = self._api(f"/calendar?start={start}&end={end}")
        if not cal or not isinstance(cal, list):
            warn("No upcoming releases in the next 14 days.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        print(f"\n  {C.BOLD}{self._display} Calendar{C.RESET} (next 14 days)\n")
        for item in cal:
            self._print_calendar_item(item)

        print()
        input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

    def _print_calendar_item(self, item):
        """Override in subclass."""
        title = item.get("title", "?")
        date = item.get("airDateUtc", item.get("releaseDate", "?"))[:10]
        print(f"  {date}  {title}")

    # ── Activity ──────────────────────────────────────────────────────────

    def _view_activity(self):
        history = self._api("/history?page=1&pageSize=20&sortDirection=descending&sortKey=date")
        if not history or not history.get("records"):
            warn("No recent activity.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        print(f"\n  {C.BOLD}{self._display} Recent Activity{C.RESET}\n")
        for r in history["records"][:20]:
            event = r.get("eventType", "?")
            date = r.get("date", "?")[:10]
            title = self._activity_title(r)

            if event == "grabbed":
                icon = f"{C.ACCENT}↓{C.RESET}"
            elif event in ("downloadFolderImported", "trackFileImported", "albumImportIncomplete"):
                icon = f"{C.GREEN}✓{C.RESET}"
            elif event in ("downloadFailed",):
                icon = f"{C.RED}✗{C.RESET}"
            else:
                icon = f"{C.DIM}·{C.RESET}"

            print(f"  {icon} {date}  {event:<28} {title}")

        print()
        input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

    def _activity_title(self, record):
        """Override in subclass for richer titles."""
        return record.get("sourceTitle", "?")[:60]

    # ── System ────────────────────────────────────────────────────────────

    def _system_status(self):
        status = self._api("/system/status")
        health = self._api("/health")
        disk = self._api("/diskspace")

        print(f"\n  {C.BOLD}{self._display} System Status{C.RESET}\n")

        if status:
            print(f"  {C.BOLD}Version:{C.RESET}  {status.get('version', '?')}")
            print(f"  {C.BOLD}Branch:{C.RESET}   {status.get('branch', '?')}")
            print(f"  {C.BOLD}OS:{C.RESET}       {status.get('osName', '?')}")
            print(f"  {C.BOLD}Runtime:{C.RESET}  {status.get('runtimeName', '?')} {status.get('runtimeVersion', '')}")
            print(f"  {C.BOLD}Startup:{C.RESET}  {status.get('startTime', '?')[:19]}")

        if disk and isinstance(disk, list):
            print(f"\n  {C.BOLD}Disk Space:{C.RESET}")
            for d in disk:
                path = d.get("path", "?")
                free = d.get("freeSpace", 0) / (1024 ** 3)
                total = d.get("totalSpace", 0) / (1024 ** 3)
                pct = ((total - free) / total * 100) if total > 0 else 0
                color = C.RED if pct > 90 else C.YELLOW if pct > 80 else C.RESET
                print(f"    {path:<30} {color}{pct:.0f}%{C.RESET} used  ({free:.1f}GB free)")

        if health and isinstance(health, list):
            if health:
                print(f"\n  {C.BOLD}Health Issues:{C.RESET}")
                for h in health:
                    level = h.get("type", "?").lower()
                    msg = h.get("message", "?")
                    icon = f"{C.RED}●{C.RESET}" if level == "error" else f"{C.YELLOW}●{C.RESET}"
                    print(f"    {icon} {msg}")
            else:
                print(f"\n  {C.GREEN}No health issues{C.RESET}")

        print()
        input(f"  {C.DIM}Press Enter to continue...{C.RESET}")

    # ── Search & Add ──────────────────────────────────────────────────────

    def _search_and_add(self):
        """Search for new media and add to library."""
        while True:
            term = prompt_text(f"Search {self._display} for {self._media_label}:")
            if not term:
                return

            results = self._api(f"{self._media_endpoint}/lookup?term={term}")
            if not results or not isinstance(results, list):
                warn(f"No results for '{term}'.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
                continue

            # Filter out items already in the library
            existing = self._api(self._media_endpoint)
            existing_ids = set()
            if existing and isinstance(existing, list):
                for item in existing:
                    existing_ids.update(self._get_item_ids(item))

            choices = []
            filtered = []
            for item in results[:20]:
                ids = self._get_item_ids(item)
                in_lib = bool(ids & existing_ids)
                label = self._format_search_result(item)
                if in_lib:
                    label += f"  {C.GREEN}(in library){C.RESET}"
                choices.append(label)
                filtered.append((item, in_lib))

            choices.append("← Back")
            idx = pick_option(f"Results for '{term}':", choices)
            if idx >= len(filtered):
                return

            item, in_lib = filtered[idx]
            if in_lib:
                warn("Already in your library.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
                continue

            self._add_media(item)

    def _get_item_ids(self, item):
        """Return a set of unique IDs for dedup. Override in subclass."""
        ids = set()
        for key in ("tvdbId", "tmdbId", "id"):
            val = item.get(key)
            if val:
                ids.add(f"{key}:{val}")
        # Lidarr uses foreignArtistId
        fid = item.get("foreignArtistId")
        if fid:
            ids.add(f"foreignArtistId:{fid}")
        return ids

    def _format_search_result(self, item):
        """Override in subclass for custom search result display."""
        title = item.get("title", item.get("artistName", "?"))
        year = item.get("year", "")
        year_str = f" ({year})" if year else ""
        return f"{title}{year_str}"

    def _add_media(self, item):
        """Add a searched item to the library."""
        title = self._format_search_result(item)
        print(f"\n  {C.BOLD}Add: {title}{C.RESET}")

        # Fetch root folders
        root_folders = self._api("/rootfolder")
        if not root_folders or not isinstance(root_folders, list):
            error("No root folders configured.")
            return

        if len(root_folders) == 1:
            root_path = root_folders[0].get("path", "")
        else:
            rf_choices = [f.get("path", "?") for f in root_folders]
            rf_choices.append("Cancel")
            rf_idx = pick_option("Root folder:", rf_choices)
            if rf_idx >= len(root_folders):
                return
            root_path = root_folders[rf_idx].get("path", "")

        # Fetch quality profiles
        quality_profiles = self._api("/qualityprofile")
        if not quality_profiles or not isinstance(quality_profiles, list):
            error("No quality profiles found.")
            return

        if len(quality_profiles) == 1:
            qp_id = quality_profiles[0].get("id", 1)
        else:
            qp_choices = [q.get("name", "?") for q in quality_profiles]
            qp_choices.append("Cancel")
            qp_idx = pick_option("Quality profile:", qp_choices)
            if qp_idx >= len(quality_profiles):
                return
            qp_id = quality_profiles[qp_idx].get("id", 1)

        # Build the payload — subclasses override for specific fields
        payload = self._build_add_payload(item, root_path, qp_id)
        if not payload:
            return

        search_on_add = confirm("Search for downloads immediately?")
        payload["addOptions"] = self._add_options(search_on_add)

        result = self._api(self._media_endpoint, method="POST", data=payload)
        if result and result.get("id"):
            success(f"Added: {title}")
        else:
            error(f"Failed to add {title}.")

        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")

    def _build_add_payload(self, item, root_path, quality_profile_id):
        """Build POST payload to add media. Override in subclass."""
        return {
            **item,
            "rootFolderPath": root_path,
            "qualityProfileId": quality_profile_id,
            "monitored": True,
        }

    def _add_options(self, search_on_add):
        """Return addOptions dict. Override in subclass."""
        return {"searchForMissingEpisodes": search_on_add}
