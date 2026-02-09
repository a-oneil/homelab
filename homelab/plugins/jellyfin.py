"""Jellyfin media server plugin — library scanning."""

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.transport import ssh_run
from homelab.ui import pick_option, info, success, error


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

    def get_menu_items(self):
        return [
            ("Jellyfin             — scan and manage media libraries", jellyfin_menu),
        ]

    def get_actions(self):
        return {
            "Jellyfin Scan Libraries": ("jellyfin_scan", jellyfin_scan),
        }


def jellyfin_menu():
    while True:
        idx = pick_option("Jellyfin:", [
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
            add_plugin_favorite(JellyfinPlugin())
        elif idx == 0:
            jellyfin_scan()


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
