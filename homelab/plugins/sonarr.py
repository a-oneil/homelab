"""Sonarr plugin — TV series library, queue, calendar."""

from homelab.plugins.arr import ArrPlugin
from homelab.ui import C


class SonarrPlugin(ArrPlugin):
    _service = "sonarr"
    _display = "Sonarr"
    _api_version = "v3"
    _media_endpoint = "/series"
    _media_label = "series"
    _media_singular = "series"

    def _menu_description(self):
        return "TV series, queue, calendar"

    def _format_media_line(self, item):
        title = item.get("title", "?")
        monitored = f"{C.GREEN}●{C.RESET}" if item.get("monitored") else f"{C.DIM}○{C.RESET}"
        stats = item.get("statistics", {})
        eps = stats.get("episodeFileCount", 0)
        total = stats.get("totalEpisodeCount", 0)
        pct = f"{eps}/{total}" if total else "?"
        status = item.get("status", "?")
        return f"{monitored} {title:<40} {pct:>8} eps  [{status}]"

    def _print_media_details(self, item):
        print(f"  Status: {item.get('status', '?')}")
        print(f"  Network: {item.get('network', '?')}")
        print(f"  Year: {item.get('year', '?')}")
        stats = item.get("statistics", {})
        eps = stats.get("episodeFileCount", 0)
        total = stats.get("totalEpisodeCount", 0)
        size_gb = stats.get("sizeOnDisk", 0) / (1024 ** 3)
        print(f"  Episodes: {eps}/{total}")
        print(f"  Size: {size_gb:.1f} GB")
        path = item.get("path", "")
        if path:
            print(f"  Path: {path}")
        genres = item.get("genres", [])
        if genres:
            print(f"  Genres: {', '.join(genres[:5])}")

    def _print_calendar_item(self, item):
        series = item.get("series", {}).get("title", "?")
        season = item.get("seasonNumber", "?")
        episode = item.get("episodeNumber", "?")
        title = item.get("title", "")
        date = item.get("airDateUtc", "?")[:10]
        ep_str = f"S{season:02d}E{episode:02d}" if isinstance(season, int) else f"S{season}E{episode}"
        line = f"  {date}  {series} — {ep_str}"
        if title:
            line += f" {title}"
        print(line)

    def _activity_title(self, record):
        series = record.get("series", {}).get("title", "")
        episode = record.get("episode", {})
        if series and episode:
            s = episode.get("seasonNumber", "?")
            e = episode.get("episodeNumber", "?")
            return f"{series} S{s:02d}E{e:02d}" if isinstance(s, int) else f"{series}"
        return record.get("sourceTitle", "?")[:60]

    def _format_search_result(self, item):
        title = item.get("title", "?")
        year = item.get("year", "")
        network = item.get("network", "")
        seasons = item.get("seasonCount", item.get("seasons", "?"))
        if isinstance(seasons, list):
            seasons = len(seasons)
        parts = [title]
        if year:
            parts.append(f"({year})")
        if network:
            parts.append(f"[{network}]")
        if seasons:
            parts.append(f"{seasons} seasons")
        return " ".join(parts)

    def _build_add_payload(self, item, root_path, quality_profile_id):
        return {
            "tvdbId": item.get("tvdbId"),
            "title": item.get("title", ""),
            "rootFolderPath": root_path,
            "qualityProfileId": quality_profile_id,
            "monitored": True,
            "seasonFolder": True,
        }

    def _add_options(self, search_on_add):
        return {"searchForMissingEpisodes": search_on_add}
