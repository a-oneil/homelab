"""Lidarr plugin — music artist library, queue, calendar."""

from homelab.plugins.arr import ArrPlugin
from homelab.ui import C


class LidarrPlugin(ArrPlugin):
    _service = "lidarr"
    _display = "Lidarr"
    _api_version = "v1"
    _media_endpoint = "/artist"
    _media_label = "artists"
    _media_singular = "artist"

    def _menu_description(self):
        return "music artists, queue, calendar"

    def _format_media_line(self, item):
        name = item.get("artistName", "?")
        monitored = f"{C.GREEN}●{C.RESET}" if item.get("monitored") else f"{C.DIM}○{C.RESET}"
        stats = item.get("statistics", {})
        albums = stats.get("albumCount", 0)
        tracks = stats.get("trackFileCount", 0)
        total_tracks = stats.get("totalTrackCount", 0)
        return f"{monitored} {name:<35} {albums} albums  {tracks}/{total_tracks} tracks"

    def _print_media_details(self, item):
        print(f"  Status: {item.get('status', '?')}")
        stats = item.get("statistics", {})
        albums = stats.get("albumCount", 0)
        tracks = stats.get("trackFileCount", 0)
        total_tracks = stats.get("totalTrackCount", 0)
        size_gb = stats.get("sizeOnDisk", 0) / (1024 ** 3)
        print(f"  Albums: {albums}")
        print(f"  Tracks: {tracks}/{total_tracks}")
        print(f"  Size: {size_gb:.1f} GB")
        path = item.get("path", "")
        if path:
            print(f"  Path: {path}")
        genres = item.get("genres", [])
        if genres:
            print(f"  Genres: {', '.join(genres[:5])}")

    def _print_calendar_item(self, item):
        title = item.get("title", "?")
        artist = item.get("artist", {}).get("artistName", "?")
        date = item.get("releaseDate", "?")
        if date and len(date) >= 10:
            date = date[:10]
        album_type = item.get("albumType", "")
        type_str = f" [{album_type}]" if album_type else ""
        print(f"  {date}  {artist} — {title}{type_str}")

    def _activity_title(self, record):
        artist = record.get("artist", {}).get("artistName", "")
        album = record.get("album", {}).get("title", "")
        if artist and album:
            return f"{artist} — {album}"
        return record.get("sourceTitle", "?")[:60]

    def _format_search_result(self, item):
        name = item.get("artistName", "?")
        genres = item.get("genres", [])
        genre_str = f" [{', '.join(genres[:2])}]" if genres else ""
        return f"{name}{genre_str}"

    def _build_add_payload(self, item, root_path, quality_profile_id):
        # Lidarr also needs metadataProfileId
        meta_profiles = self._api("/metadataprofile")
        meta_id = 1
        if meta_profiles and isinstance(meta_profiles, list) and meta_profiles:
            meta_id = meta_profiles[0].get("id", 1)
        return {
            "foreignArtistId": item.get("foreignArtistId", ""),
            "artistName": item.get("artistName", ""),
            "rootFolderPath": root_path,
            "qualityProfileId": quality_profile_id,
            "metadataProfileId": meta_id,
            "monitored": True,
        }

    def _add_options(self, search_on_add):
        return {"searchForMissingAlbums": search_on_add}
