"""Uptime Kuma plugin — monitor status overview, pause/resume monitors.

Requires the `uptime-kuma-api` package:
    pip install uptime-kuma-api
"""

import time

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": "", "alerts": []}
_CACHE_TTL = 300


def _connect():
    """Create and authenticate an Uptime Kuma API connection."""
    try:
        from uptime_kuma_api import UptimeKumaApi
    except ImportError:
        error("uptime-kuma-api not installed. Run: pip install uptime-kuma-api")
        return None

    base = CFG.get("uptimekuma_url", "").rstrip("/")
    username = CFG.get("uptimekuma_username", "")
    password = CFG.get("uptimekuma_password", "")
    if not base or not username or not password:
        return None

    try:
        api = UptimeKumaApi(base, timeout=10)
        api.login(username, password)
        return api
    except Exception as e:
        error(f"Uptime Kuma connection error: {e}")
        return None


class UptimeKumaPlugin(Plugin):
    name = "Uptime Kuma"
    key = "uptimekuma"

    def is_configured(self):
        return bool(
            CFG.get("uptimekuma_url")
            and CFG.get("uptimekuma_username")
            and CFG.get("uptimekuma_password")
        )

    def get_config_fields(self):
        return [
            ("uptimekuma_url", "Kuma URL", "e.g. http://192.168.1.100:3001", False),
            ("uptimekuma_username", "Kuma Username", "login username", False),
            ("uptimekuma_password", "Kuma Password", "login password", True),
        ]

    def get_health_alerts(self):
        """Return alerts for any down monitors (uses shared cache)."""
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _refresh_cache()
        return list(_HEADER_CACHE.get("alerts", []))

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _refresh_cache()
        return _HEADER_CACHE.get("stats") or None

    def get_dashboard_widgets(self):
        api = _connect()
        if not api:
            return []
        try:
            monitors = api.get_monitors()
            total = len(monitors)
            up = sum(1 for m in monitors if m.get("active"))
            down = [m for m in monitors if not m.get("active")]
            lines = [f"{up}/{total} monitors up"]
            for m in down[:5]:
                name = m.get("name", "?")
                lines.append(f"  {C.RED}●{C.RESET} {name} — down")
            if not down:
                lines.append(f"{C.GREEN}All monitors healthy{C.RESET}")
            return [{"title": "Uptime Kuma", "lines": lines}]
        except Exception:
            return []
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    def get_menu_items(self):
        return [
            ("Uptime Kuma          — monitor status and management", kuma_menu),
        ]

    def get_actions(self):
        return {
            "Kuma Monitor Overview": ("kuma_overview", _monitor_overview),
            "Service Health Map": ("health_map", _launch_health_map),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "kuma_monitor":
            monitor_id = int(fav["id"])
            return lambda mid=monitor_id: _monitor_detail(mid)


def _refresh_cache():
    """Single connection to fetch both stats and health alerts for the header."""
    _HEADER_CACHE["timestamp"] = time.time()  # Set early to prevent concurrent calls
    api = _connect()
    if not api:
        return
    try:
        monitors = api.get_monitors()
        total = len(monitors)
        up = sum(1 for m in monitors if m.get("active"))
        down_count = total - up

        # Stats line
        if down_count > 0:
            _HEADER_CACHE["stats"] = f"Kuma: {up}/{total} up ({C.RED}{down_count} down{C.RESET})"
        else:
            _HEADER_CACHE["stats"] = f"Kuma: {total} monitors, all up"

        # Health alerts for down monitors
        if down_count > 0:
            down_names = [m.get("name", f"#{m.get('id', '?')}") for m in monitors if not m.get("active")]
            names = ", ".join(down_names[:3])
            if len(down_names) > 3:
                names += f" +{len(down_names) - 3} more"
            _HEADER_CACHE["alerts"] = [f"{C.RED}Kuma down:{C.RESET} {names}"]
        else:
            _HEADER_CACHE["alerts"] = []
    except Exception:
        pass
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def _launch_health_map():
    """Launch the service health map from within Uptime Kuma."""
    from homelab.healthmap import health_map
    # Import PLUGINS from main to pass to health_map
    from homelab.main import PLUGINS
    health_map(PLUGINS)


def kuma_menu():
    while True:
        idx = pick_option("Uptime Kuma:", [
            "Monitor Overview     — all monitors with status",
            "Down Monitors        — only show failing monitors",
            "Service Health Map   — live status of all services",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 5:
            return
        elif idx == 3:
            continue
        elif idx == 4:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UptimeKumaPlugin())
        elif idx == 0:
            _monitor_overview()
        elif idx == 1:
            _monitor_overview(down_only=True)
        elif idx == 2:
            _launch_health_map()


def _monitor_overview(down_only=False):
    """Show all monitors with their current status."""
    api = _connect()
    if not api:
        return

    try:
        monitors = api.get_monitors()
    except Exception as e:
        error(f"Could not fetch monitors: {e}")
        return
    finally:
        try:
            api.disconnect()
        except Exception:
            pass

    if down_only:
        monitors = [m for m in monitors if not m.get("active")]

    if not monitors:
        if down_only:
            success("All monitors are up!")
        else:
            warn("No monitors found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    title = "Down Monitors" if down_only else "All Monitors"
    print(f"\n  {C.BOLD}{title}{C.RESET}\n")

    choices = []
    for m in monitors:
        active = m.get("active", False)
        name = m.get("name", f"Monitor #{m.get('id', '?')}")
        mtype = m.get("type", "?")
        url = m.get("url", "")
        interval = m.get("interval", 60)

        if active:
            icon = f"{C.GREEN}●{C.RESET}"
            status_str = "active"
        else:
            icon = f"{C.RED}●{C.RESET}"
            status_str = "paused"

        label = f"{icon} {name:<30} [{mtype}] {status_str}  ({interval}s)"
        if url:
            label = f"{icon} {name:<30} [{mtype}] {status_str}"
        choices.append(label)

    choices.append("← Back")
    idx = pick_option("", choices)
    if idx < len(monitors):
        _monitor_detail(monitors[idx].get("id"))


def _monitor_detail(monitor_id):
    """Show detail for a single monitor."""
    api = _connect()
    if not api:
        return

    try:
        monitor = api.get_monitor(monitor_id)
    except Exception as e:
        error(f"Could not fetch monitor: {e}")
        return
    finally:
        try:
            api.disconnect()
        except Exception:
            pass

    m = monitor.get("monitor", monitor)
    name = m.get("name", f"Monitor #{monitor_id}")
    mtype = m.get("type", "?")
    url = m.get("url", "")
    hostname = m.get("hostname", "")
    port = m.get("port", "")
    active = m.get("active", False)
    interval = m.get("interval", 60)
    max_retries = m.get("maxretries", 0)

    print(f"\n  {C.BOLD}{name}{C.RESET}")
    print(f"  Type: {mtype}")
    if url:
        print(f"  URL: {url}")
    if hostname:
        target = f"{hostname}:{port}" if port else hostname
        print(f"  Host: {target}")
    status_str = f"{C.GREEN}Active{C.RESET}" if active else f"{C.RED}Paused{C.RESET}"
    print(f"  Status: {status_str}")
    print(f"  Interval: {interval}s  Max retries: {max_retries}")

    # Show heartbeats if available
    beats = monitor.get("importantHeartBeatList", [])
    if beats:
        print(f"\n  {C.BOLD}Recent Events:{C.RESET}")
        for beat in beats[:10]:
            ts = beat.get("time", "?")
            s = beat.get("status", 0)
            msg = beat.get("msg", "")
            icon = f"{C.GREEN}●{C.RESET}" if s == 1 else f"{C.RED}●{C.RESET}"
            line = f"    {icon} {ts}"
            if msg:
                line += f"  {msg[:60]}"
            print(line)

    toggle_label = "Pause" if active else "Resume"
    choices = [toggle_label, "★ Favorite", "← Back"]
    aidx = pick_option(f"{name}:", choices)
    al = choices[aidx]

    if al == "← Back":
        return
    elif al == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("kuma_monitor", str(monitor_id), f"Kuma: {name}")
    elif al in ("Pause", "Resume"):
        api2 = _connect()
        if not api2:
            return
        try:
            if active:
                api2.pause_monitor(monitor_id)
                success(f"Paused: {name}")
            else:
                api2.resume_monitor(monitor_id)
                success(f"Resumed: {name}")
        except Exception as e:
            error(f"Failed: {e}")
        finally:
            try:
                api2.disconnect()
            except Exception:
                pass
