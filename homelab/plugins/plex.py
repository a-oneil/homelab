"""Plex media server plugin — library scanning."""

import json

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.transport import ssh_run
from homelab.ui import pick_option, info, success, error, warn


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

    def get_menu_items(self):
        return [
            ("Plex                 — scan and manage media libraries", plex_menu),
        ]

    def get_actions(self):
        return {
            "Plex Scan Libraries": ("plex_scan", plex_scan),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "plex_library":
            key = fav["id"]
            return lambda k=key: _scan_library(k)


def plex_menu():
    while True:
        idx = pick_option("Plex:", [
            "Scan Libraries       — trigger library refresh",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 3:
            return
        elif idx == 1:
            continue
        elif idx == 2:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(PlexPlugin())
        elif idx == 0:
            plex_scan()


def plex_scan():
    """Trigger a Plex library scan."""
    url = CFG.get("plex_url", "").rstrip("/")
    token = CFG.get("plex_token", "")
    if not url or not token:
        error("Plex not configured. Set URL and token in Settings.")
        return

    info("Fetching Plex libraries...")

    result = ssh_run(
        f"curl -s '{url}/library/sections' "
        f"-H 'X-Plex-Token: {token}' "
        f"-H 'Accept: application/json'"
    )
    if result.returncode != 0:
        error("Failed to connect to Plex.")
        return

    try:
        data = json.loads(result.stdout)
        directories = data.get("MediaContainer", {}).get("Directory", [])
    except (json.JSONDecodeError, KeyError):
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

    if not directories:
        warn("No Plex libraries found.")
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
