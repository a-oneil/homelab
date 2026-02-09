"""Plugin base class and registry."""

from homelab.config import CFG, save_config
from homelab.ui import pick_option, success, warn


def add_plugin_favorite(plugin):
    """Let user pin a plugin action to the main menu."""
    all_actions = plugin.get_actions()
    favs = CFG.get("favorites", [])

    # Filter out already-favorited string keys
    fav_keys = {f for f in favs if isinstance(f, str)}
    available = [(name, key) for name, (key, func) in all_actions.items() if key not in fav_keys]
    if not available:
        warn("All actions are already favorites.")
        return

    choices = [name for name, key in available]
    choices.append("Cancel")
    idx = pick_option("Pin which action to the main menu?", choices)
    if idx >= len(available):
        return

    name, key = available[idx]
    CFG.setdefault("favorites", []).append(key)
    save_config(CFG)
    success(f"Pinned: ★ {name}")


def add_item_favorite(fav_type, item_id, name, **extra):
    """Pin a specific item (entity, container, etc.) to the main menu."""
    favs = CFG.get("favorites", [])
    # Check for duplicate
    for f in favs:
        if isinstance(f, dict) and f.get("type") == fav_type and f.get("id") == item_id:
            warn(f"{name} is already a favorite.")
            return
    fav = {"type": fav_type, "id": item_id, "name": name, **extra}
    CFG.setdefault("favorites", []).append(fav)
    save_config(CFG)
    success(f"Pinned: ★ {name}")


class Plugin:
    """Base class for homelab service plugins."""
    name = ""           # Display name, e.g. "Unraid"
    key = ""            # Config key prefix, e.g. "unraid"

    def is_configured(self) -> bool:
        """Return True if required config fields are set."""
        return False

    def get_default_config(self) -> dict:
        """Return default config values for this plugin."""
        return {}

    def get_config_fields(self) -> list:
        """Return [(key, label, hint, is_secret), ...] for settings menu."""
        return []

    def get_health_alerts(self) -> list:
        """Return list of alert strings for the header (e.g. down monitors)."""
        return []

    def get_header_stats(self) -> str | None:
        """Return one-line stats for the header, or None if unavailable."""
        return None

    def get_dashboard_widgets(self) -> list:
        """Return list of widget dicts for the status dashboard.

        Each widget: {"title": str, "lines": [str, ...]}
        """
        return []

    def get_menu_items(self) -> list:
        """Return [(label, function), ...] for main menu."""
        return []

    def get_actions(self) -> dict:
        """Return {display_name: (key, function)} for favorites."""
        return {}

    def resolve_favorite(self, fav: dict):
        """Resolve a parameterized favorite to a callable, or None."""
        return None
