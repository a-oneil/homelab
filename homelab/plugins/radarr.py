"""Radarr plugin — movie library, queue, calendar."""

from homelab.plugins.arr import ArrPlugin
from homelab.ui import C


class RadarrPlugin(ArrPlugin):
    _service = "radarr"
    _display = "Radarr"
    _api_version = "v3"
    _media_endpoint = "/movie"
    _media_label = "movies"
    _media_singular = "movie"

    def _menu_description(self):
        return "movies, queue, calendar"

    def _format_media_line(self, item):
        title = item.get("title", "?")
        year = item.get("year", "")
        monitored = f"{C.GREEN}●{C.RESET}" if item.get("monitored") else f"{C.DIM}○{C.RESET}"
        has_file = f"{C.GREEN}✓{C.RESET}" if item.get("hasFile") else f"{C.DIM}✗{C.RESET}"
        year_str = f" ({year})" if year else ""
        return f"{monitored} {has_file} {title}{year_str}"

    def _print_media_details(self, item):
        print(f"  Year: {item.get('year', '?')}")
        print(f"  Status: {item.get('status', '?')}")
        print(f"  Studio: {item.get('studio', '?')}")
        runtime = item.get("runtime", 0)
        if runtime:
            print(f"  Runtime: {runtime} min")
        has_file = item.get("hasFile", False)
        print(f"  On Disk: {'Yes' if has_file else 'No'}")
        if has_file:
            size_gb = item.get("sizeOnDisk", 0) / (1024 ** 3)
            print(f"  Size: {size_gb:.1f} GB")
        path = item.get("path", "")
        if path:
            print(f"  Path: {path}")
        genres = item.get("genres", [])
        if genres:
            print(f"  Genres: {', '.join(genres[:5])}")
        rating = item.get("ratings", {}).get("tmdb", {}).get("value", "")
        if rating:
            print(f"  TMDB: {rating}/10")

    def _print_calendar_item(self, item):
        title = item.get("title", "?")
        year = item.get("year", "")
        date = item.get("inCinemas", item.get("digitalRelease", item.get("physicalRelease", "?")))
        if date and len(date) >= 10:
            date = date[:10]
        year_str = f" ({year})" if year else ""
        has_file = f"{C.GREEN}✓{C.RESET}" if item.get("hasFile") else f"{C.DIM}✗{C.RESET}"
        print(f"  {date}  {has_file} {title}{year_str}")

    def _activity_title(self, record):
        movie = record.get("movie", {})
        title = movie.get("title", "")
        year = movie.get("year", "")
        if title:
            return f"{title} ({year})" if year else title
        return record.get("sourceTitle", "?")[:60]

    def _format_search_result(self, item):
        title = item.get("title", "?")
        year = item.get("year", "")
        studio = item.get("studio", "")
        runtime = item.get("runtime", 0)
        parts = [title]
        if year:
            parts.append(f"({year})")
        if studio:
            parts.append(f"[{studio}]")
        if runtime:
            parts.append(f"{runtime}min")
        return " ".join(parts)

    def _build_add_payload(self, item, root_path, quality_profile_id):
        return {
            "tmdbId": item.get("tmdbId"),
            "title": item.get("title", ""),
            "rootFolderPath": root_path,
            "qualityProfileId": quality_profile_id,
            "monitored": True,
        }

    def _add_options(self, search_on_add):
        return {"searchForMovie": search_on_add}
