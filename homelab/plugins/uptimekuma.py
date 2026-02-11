"""Uptime Kuma plugin — monitor management, maintenance, notifications, status pages.

Requires the `uptime-kuma-api` package:
    pip install uptime-kuma-api
"""

import time
from datetime import datetime, timezone

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import (C, pick_option, pick_multi, bar_chart, sparkline,
                        success, error, warn, info, confirm, prompt_text)
from homelab.auditlog import log_action

_HEADER_CACHE = {"timestamp": 0, "stats": "", "alerts": []}
_CACHE_TTL = 300

# Heartbeat status codes from uptime-kuma-api MonitorStatus enum
_STATUS_DOWN = 0
_STATUS_UP = 1
_STATUS_PENDING = 2
_STATUS_MAINTENANCE = 3

# Monitor type display names
_TYPE_LABELS = {
    "http": "http", "port": "tcp", "ping": "ping", "keyword": "keyword",
    "dns": "dns", "docker": "docker", "push": "push", "steam": "steam",
    "mqtt": "mqtt", "sqlserver": "sql", "postgres": "pg", "mysql": "mysql",
    "mongodb": "mongo", "redis": "redis", "group": "group",
    "json-query": "json", "grpc-keyword": "grpc", "real-browser": "browser",
    "gamedig": "game", "radius": "radius", "kafka-producer": "kafka",
    "tailscale-ping": "ts-ping",
}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

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


def _disconnect(api):
    """Safely disconnect an API session."""
    try:
        api.disconnect()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _get_monitors_with_status(api):
    """Get monitors enriched with heartbeat UP/DOWN status.

    The 'active' field on monitors only indicates enabled vs paused —
    actual UP/DOWN comes from heartbeat data.
    """
    monitors = api.get_monitors()
    heartbeats = api.get_heartbeats()
    for m in monitors:
        mid = m.get("id")
        beats = heartbeats.get(mid, [])
        if beats:
            latest = beats[-1]
            m["_hb_status"] = latest.get("status", None)
            m["_hb_ping"] = latest.get("ping", None)
            m["_hb_msg"] = latest.get("msg", "")
        else:
            m["_hb_status"] = None
            m["_hb_ping"] = None
            m["_hb_msg"] = ""
    return monitors


def _is_up(m):
    """Check if a monitor's service is actually UP based on heartbeat."""
    return m.get("_hb_status") == _STATUS_UP


def _is_down(m):
    """Check if a monitor's service is DOWN (active but failing)."""
    return m.get("active", False) and m.get("_hb_status") == _STATUS_DOWN


def _fetch_enrichment_data(api):
    """Fetch uptime, avg_ping, and cert_info in one batch."""
    try:
        uptime_data = api.uptime()
    except Exception:
        uptime_data = {}
    try:
        avg_ping_data = api.avg_ping()
    except Exception:
        avg_ping_data = {}
    try:
        cert_data = api.cert_info()
    except Exception:
        cert_data = {}
    return uptime_data, avg_ping_data, cert_data


def _cert_days_remaining(cert):
    """Extract days remaining from a cert_info entry."""
    if not cert:
        return None
    ci = cert.get("certInfo") or cert
    days = ci.get("daysRemaining")
    if days is not None:
        return int(days)
    expiry = ci.get("expiryDate") or ci.get("validTo")
    if expiry:
        try:
            exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            return (exp_dt - datetime.now(timezone.utc)).days
        except Exception:
            pass
    return None


def _status_icon(m):
    """Return (icon, status_str) for a monitor based on heartbeat and active state."""
    active = m.get("active", False)
    hb = m.get("_hb_status")
    if not active:
        return f"{C.DIM}○{C.RESET}", "paused"
    if hb == _STATUS_UP:
        return f"{C.GREEN}●{C.RESET}", "up"
    if hb == _STATUS_DOWN:
        return f"{C.RED}●{C.RESET}", "down"
    if hb == _STATUS_PENDING:
        return f"{C.YELLOW}●{C.RESET}", "pending"
    if hb == _STATUS_MAINTENANCE:
        return f"{C.YELLOW}●{C.RESET}", "maint"
    return f"{C.DIM}○{C.RESET}", "unknown"


def _type_label(m):
    """Short type label for a monitor."""
    raw = m.get("type", "?")
    if isinstance(raw, str):
        return _TYPE_LABELS.get(raw, raw)
    return str(getattr(raw, "value", raw))


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

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
            monitors = _get_monitors_with_status(api)
            uptime_data = api.uptime()
            active = [m for m in monitors if m.get("active")]
            paused = [m for m in monitors if not m.get("active")]
            up = sum(1 for m in active if _is_up(m))
            down = [m for m in active if _is_down(m)]
            # Average uptime
            avg_24h = 0
            if uptime_data:
                vals = [v.get(24, 0) for v in uptime_data.values()]
                avg_24h = sum(vals) / len(vals) * 100 if vals else 0
            lines = [f"{up}/{len(active)} monitors up  ({avg_24h:.1f}% avg)"]
            if paused:
                lines[0] += f"  {C.DIM}({len(paused)} paused){C.RESET}"
            for m in down[:5]:
                lines.append(f"  {C.RED}●{C.RESET} {m.get('name', '?')} — down")
            if not down:
                lines.append(f"{C.GREEN}All monitors healthy{C.RESET}")
            return [{"title": "Uptime Kuma", "lines": lines}]
        except Exception:
            return []
        finally:
            _disconnect(api)

    def get_menu_items(self):
        return [
            ("Uptime Kuma          — monitor status and management", kuma_menu),
        ]

    def get_actions(self):
        return {
            "Kuma Monitors": ("kuma_monitors", _monitor_overview),
            "Kuma Maintenance": ("kuma_maintenance", _maintenance_menu),
            "Service Health Map": ("health_map", _launch_health_map),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "kuma_monitor":
            monitor_id = int(fav["id"])
            return lambda mid=monitor_id: _monitor_detail(mid)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _refresh_cache():
    """Fetch stats and health alerts for the header."""
    _HEADER_CACHE["timestamp"] = time.time()
    api = _connect()
    if not api:
        return
    try:
        monitors = _get_monitors_with_status(api)
        uptime_data = api.uptime()
        active = [m for m in monitors if m.get("active")]
        up_count = sum(1 for m in active if _is_up(m))
        down_monitors = [m for m in active if _is_down(m)]
        down_count = len(down_monitors)
        # Average uptime
        avg_str = ""
        if uptime_data:
            vals = [v.get(24, 0) for v in uptime_data.values()]
            avg_24h = sum(vals) / len(vals) * 100 if vals else 0
            avg_str = f" | {avg_24h:.1f}%"
        if down_count > 0:
            _HEADER_CACHE["stats"] = (
                f"Kuma: {up_count}/{len(active)} up "
                f"({C.RED}{down_count} down{C.RESET}){avg_str}"
            )
            down_names = [m.get("name", f"#{m.get('id', '?')}") for m in down_monitors]
            names = ", ".join(down_names[:3])
            if len(down_names) > 3:
                names += f" +{len(down_names) - 3} more"
            _HEADER_CACHE["alerts"] = [f"{C.RED}Kuma down:{C.RESET} {names}"]
        else:
            _HEADER_CACHE["stats"] = f"Kuma: {len(active)} monitors, all up{avg_str}"
            _HEADER_CACHE["alerts"] = []
    except Exception:
        pass
    finally:
        _disconnect(api)


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def _launch_health_map():
    from homelab.healthmap import health_map
    from homelab.main import PLUGINS
    health_map(PLUGINS)


def kuma_menu():
    while True:
        idx = pick_option("Uptime Kuma:", [
            "Monitors             — overview, create, bulk ops, stats",
            "Maintenance          — schedule and manage downtime",
            "Administration       — notifications, status pages, server info",
            "Service Health Map   — live status of all services",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 6:
            return
        elif idx == 4:
            continue
        elif idx == 5:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UptimeKumaPlugin())
        elif idx == 0:
            _monitor_overview()
        elif idx == 1:
            _maintenance_menu()
        elif idx == 2:
            _admin_menu()
        elif idx == 3:
            _launch_health_map()


# ---------------------------------------------------------------------------
# Administration submenu
# ---------------------------------------------------------------------------

def _admin_menu():
    """Combined submenu for notifications, status pages, and server info."""
    while True:
        idx = pick_option("Administration:", [
            "Notifications    — view and test alert channels",
            "Status Pages     — manage public status pages",
            "Server Info      — version, database, shrink",
            "← Back",
        ])
        if idx == 3:
            return
        elif idx == 0:
            _notifications_menu()
        elif idx == 1:
            _status_pages_menu()
        elif idx == 2:
            _server_info()


# ---------------------------------------------------------------------------
# Monitor overview (enriched)
# ---------------------------------------------------------------------------

def _monitor_overview(down_only=False):
    """Hub view: stats header + monitor list + actions at bottom."""
    while True:
        api = _connect()
        if not api:
            return
        try:
            monitors = _get_monitors_with_status(api)
            uptime_data, avg_ping_data, cert_data = _fetch_enrichment_data(api)
        except Exception as e:
            error(f"Could not fetch monitors: {e}")
            return
        finally:
            _disconnect(api)

        all_monitors = monitors
        active = [m for m in monitors if m.get("active")]
        paused = [m for m in monitors if not m.get("active")]
        up_count = sum(1 for m in active if _is_up(m))
        down_list = [m for m in active if _is_down(m)]

        # Build stats header
        hdr = []
        hdr.append(
            f"\n  {C.ACCENT}{C.BOLD}Monitors{C.RESET}"
            f"  {len(all_monitors)} total  |  {C.GREEN}{up_count} up{C.RESET}"
            f"  {C.RED}{len(down_list)} down{C.RESET}"
            f"  {C.DIM}{len(paused)} paused{C.RESET}")
        if uptime_data:
            vals_24 = [v.get(24, 0) for v in uptime_data.values()]
            vals_30d = [v.get(720, 0) for v in uptime_data.values()]
            avg_24 = sum(vals_24) / len(vals_24) * 100 if vals_24 else 0
            avg_30d = sum(vals_30d) / len(vals_30d) * 100 if vals_30d else 0
            hdr.append(f"  Avg uptime:  24h {bar_chart(avg_24, 100, width=15)} {avg_24:.1f}%"
                       f"   30d {bar_chart(avg_30d, 100, width=15)} {avg_30d:.1f}%")
        if avg_ping_data:
            pings = [v for v in avg_ping_data.values() if v]
            if pings:
                hdr.append(f"  Avg response: {sum(pings) / len(pings):.0f}ms")
        hdr.append("")
        header = "\n".join(hdr)

        # Filter
        display_monitors = [m for m in monitors if _is_down(m)] if down_only else monitors

        if not display_monitors:
            if down_only:
                header += f"\n  {C.GREEN}All monitors are up!{C.RESET}\n"
            else:
                header += f"\n  {C.DIM}No monitors found.{C.RESET}\n"

        # Build monitor rows
        choices = []
        for m in display_monitors:
            mid = m.get("id")
            icon, status_str = _status_icon(m)
            name = m.get("name", f"Monitor #{mid}")[:22]
            tl = _type_label(m)

            avg_p = avg_ping_data.get(mid)
            ping_str = f"{avg_p:>5.0f}ms" if avg_p else "     — "

            ut = uptime_data.get(mid, {})
            u24 = ut.get(24)
            if u24 is not None:
                pct = u24 * 100
                if pct >= 99:
                    up_str = f"{C.GREEN}{pct:>5.1f}%{C.RESET}"
                elif pct >= 95:
                    up_str = f"{C.YELLOW}{pct:>5.1f}%{C.RESET}"
                else:
                    up_str = f"{C.RED}{pct:>5.1f}%{C.RESET}"
            else:
                up_str = "    — "

            cert_str = ""
            cert_days = _cert_days_remaining(cert_data.get(mid))
            if cert_days is not None:
                if cert_days <= 14:
                    cert_str = f"  {C.RED}SSL:{cert_days}d{C.RESET}"
                elif cert_days <= 30:
                    cert_str = f"  {C.YELLOW}SSL:{cert_days}d{C.RESET}"
                else:
                    cert_str = f"  SSL:{cert_days}d"

            label = f"{icon} {name:<22} [{tl:<5}] {status_str:<7} {ping_str} {up_str}{cert_str}"
            choices.append(label)

        # Action items at bottom
        choices.append("───────────────")
        filter_label = "Show All Monitors" if down_only else f"Show Down Only ({len(down_list)})"
        action_start = len(display_monitors) + 1  # after separator
        choices.append(filter_label)
        choices.append("Create Monitor")
        choices.append("Bulk Pause/Resume")
        choices.append("← Back")

        title = "Down Monitors" if down_only else "Monitors"
        idx = pick_option(title, choices, header=header)

        if idx == action_start + 3:  # Back
            return
        elif idx == len(display_monitors):  # separator
            continue
        elif idx == action_start:  # Toggle filter
            down_only = not down_only
        elif idx == action_start + 1:  # Create
            _create_monitor()
        elif idx == action_start + 2:  # Bulk
            _bulk_pause_resume()
        elif idx < len(display_monitors):
            _monitor_detail(display_monitors[idx].get("id"))


# ---------------------------------------------------------------------------
# Monitor detail (enriched)
# ---------------------------------------------------------------------------

def _monitor_detail(monitor_id):
    """Show enriched detail: uptime bars, response sparkline, cert info, actions."""
    api = _connect()
    if not api:
        return
    try:
        monitors = _get_monitors_with_status(api)
        m = next((x for x in monitors if x.get("id") == monitor_id), None)
        if not m:
            error(f"Monitor #{monitor_id} not found")
            return
        important_hbs = api.get_important_heartbeats()
        beats = important_hbs.get(monitor_id, [])
        uptime_data, avg_ping_data, cert_data = _fetch_enrichment_data(api)
        # Get recent heartbeats for sparkline
        try:
            recent_beats = api.get_monitor_beats(monitor_id, 6)
        except Exception:
            recent_beats = []
    except Exception as e:
        error(f"Could not fetch monitor: {e}")
        return
    finally:
        _disconnect(api)

    name = m.get("name", f"Monitor #{monitor_id}")
    mtype = _type_label(m)
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

    # Status
    icon, status_str = _status_icon(m)
    print(f"  Status: {icon} {status_str}")

    # Ping
    current_ping = m.get("_hb_ping")
    avg_p = avg_ping_data.get(monitor_id)
    ping_parts = []
    if current_ping:
        ping_parts.append(f"{current_ping}ms")
    if avg_p:
        ping_parts.append(f"avg: {avg_p:.0f}ms")
    if ping_parts:
        print(f"  Ping: {' | '.join(ping_parts)}")

    msg = m.get("_hb_msg")
    if msg:
        print(f"  Last check: {msg}")
    print(f"  Interval: {interval}s  Max retries: {max_retries}")

    # Uptime bars
    ut = uptime_data.get(monitor_id, {})
    u24 = ut.get(24)
    u30d = ut.get(720)
    if u24 is not None or u30d is not None:
        print(f"\n  {C.BOLD}Uptime:{C.RESET}")
        if u24 is not None:
            print(f"    24h:  {bar_chart(u24 * 100, 100, width=25)}  {u24 * 100:.2f}%")
        if u30d is not None:
            print(f"    30d:  {bar_chart(u30d * 100, 100, width=25)}  {u30d * 100:.2f}%")

    # Response time sparkline
    if recent_beats:
        ping_vals = [b.get("ping") or 0 for b in recent_beats if b.get("ping")]
        if len(ping_vals) >= 2:
            min_p = min(ping_vals)
            max_p = max(ping_vals)
            avg_rt = sum(ping_vals) / len(ping_vals)
            print(f"\n  {C.BOLD}Response Time (6h):{C.RESET}")
            print(f"    {C.ACCENT}{sparkline(ping_vals, width=20)}{C.RESET}"
                  f"  min: {min_p:.0f}ms  avg: {avg_rt:.0f}ms  max: {max_p:.0f}ms")

    # SSL cert info
    cert = cert_data.get(monitor_id)
    cert_days = _cert_days_remaining(cert)
    if cert_days is not None:
        ci = (cert.get("certInfo") or cert) if cert else {}
        issuer = ci.get("issuerCN") or ci.get("issuer", {}).get("CN", "")
        expiry = ci.get("expiryDate") or ci.get("validTo", "")
        print(f"\n  {C.BOLD}SSL Certificate:{C.RESET}")
        if issuer:
            print(f"    Issuer: {issuer}")
        if cert_days <= 14:
            print(f"    Expires: {expiry}  ({C.RED}{cert_days} days remaining{C.RESET})")
        elif cert_days <= 30:
            print(f"    Expires: {expiry}  ({C.YELLOW}{cert_days} days remaining{C.RESET})")
        else:
            print(f"    Expires: {expiry}  ({cert_days} days remaining)")

    # Consecutive failures
    if recent_beats:
        consec = 0
        for b in reversed(recent_beats):
            if b.get("status") == _STATUS_DOWN:
                consec += 1
            else:
                break
        if consec > 0:
            print(f"\n  {C.RED}Consecutive failures: {consec}{C.RESET}")

    # Recent events
    if beats:
        print(f"\n  {C.BOLD}Recent Events:{C.RESET}")
        for beat in beats[-10:]:
            ts = beat.get("time", "?")
            s = beat.get("status", 0)
            beat_msg = beat.get("msg", "")
            if s == _STATUS_UP:
                ev_icon = f"{C.GREEN}●{C.RESET}"
            elif s == _STATUS_DOWN:
                ev_icon = f"{C.RED}●{C.RESET}"
            else:
                ev_icon = f"{C.YELLOW}●{C.RESET}"
            line = f"    {ev_icon} {ts}"
            if beat_msg:
                line += f"  {beat_msg[:60]}"
            print(line)

    # Actions
    toggle_label = "Pause" if active else "Resume"
    choices = [toggle_label, "Edit", "Delete", "★ Favorite", "← Back"]
    aidx = pick_option(f"{name}:", choices)
    al = choices[aidx]

    if al == "← Back":
        return
    elif al == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("kuma_monitor", str(monitor_id), f"Kuma: {name}")
    elif al == "Edit":
        _edit_monitor(monitor_id, m)
    elif al == "Delete":
        _delete_monitor(monitor_id, name)
    elif al in ("Pause", "Resume"):
        api2 = _connect()
        if not api2:
            return
        try:
            if active:
                api2.pause_monitor(monitor_id)
                log_action("Kuma Pause Monitor", name)
                success(f"Paused: {name}")
            else:
                api2.resume_monitor(monitor_id)
                log_action("Kuma Resume Monitor", name)
                success(f"Resumed: {name}")
        except Exception as e:
            error(f"Failed: {e}")
        finally:
            _disconnect(api2)


# ---------------------------------------------------------------------------
# Monitor CRUD
# ---------------------------------------------------------------------------

def _create_monitor():
    """Form to create a new monitor."""
    name = prompt_text("Monitor name:")
    if not name:
        return

    type_idx = pick_option("Monitor type:", [
        "HTTP(s)", "TCP Port", "Ping", "Keyword", "DNS", "← Cancel",
    ])
    if type_idx == 5:
        return

    type_map = {0: "http", 1: "port", 2: "ping", 3: "keyword", 4: "dns"}
    mon_type = type_map[type_idx]

    kwargs = {"name": name, "type": mon_type}

    if mon_type in ("http", "keyword"):
        url = prompt_text("URL (e.g. https://example.com):")
        if not url:
            return
        kwargs["url"] = url
        if mon_type == "keyword":
            kw = prompt_text("Keyword to search for:")
            if not kw:
                return
            kwargs["keyword"] = kw
    elif mon_type == "port":
        host = prompt_text("Hostname or IP:")
        if not host:
            return
        port_str = prompt_text("Port:")
        if not port_str:
            return
        try:
            kwargs["hostname"] = host
            kwargs["port"] = int(port_str)
        except ValueError:
            error("Port must be a number.")
            return
    elif mon_type == "ping":
        host = prompt_text("Hostname or IP:")
        if not host:
            return
        kwargs["hostname"] = host
    elif mon_type == "dns":
        host = prompt_text("DNS hostname to resolve:")
        if not host:
            return
        kwargs["hostname"] = host
        dns_server = prompt_text("DNS server (optional):", default="")
        if dns_server:
            kwargs["dns_resolve_server"] = dns_server

    interval_str = prompt_text("Check interval in seconds:", default="60")
    try:
        kwargs["interval"] = int(interval_str) if interval_str else 60
    except ValueError:
        kwargs["interval"] = 60

    retries_str = prompt_text("Max retries before down:", default="0")
    try:
        kwargs["maxretries"] = int(retries_str) if retries_str else 0
    except ValueError:
        kwargs["maxretries"] = 0

    if mon_type == "http":
        kwargs["accepted_statuscodes"] = ["200-299"]

    api = _connect()
    if not api:
        return
    try:
        api.add_monitor(**kwargs)
        log_action("Kuma Create Monitor", name)
        success(f"Created monitor: {name}")
    except Exception as e:
        error(f"Failed to create monitor: {e}")
    finally:
        _disconnect(api)


def _edit_monitor(monitor_id, monitor):
    """Edit basic fields of an existing monitor."""
    name = prompt_text("Name:", default=monitor.get("name", ""))
    if not name:
        return

    mon_type = monitor.get("type", "http")
    type_str = mon_type if isinstance(mon_type, str) else getattr(mon_type, "value", str(mon_type))

    kwargs = {
        "name": name,
        "type": type_str,
        "interval": monitor.get("interval", 60),
        "maxretries": monitor.get("maxretries", 0),
    }

    if type_str in ("http", "keyword"):
        url = prompt_text("URL:", default=monitor.get("url", ""))
        if not url:
            return
        kwargs["url"] = url
    elif type_str in ("port", "ping", "dns"):
        host = prompt_text("Hostname:", default=monitor.get("hostname", ""))
        if not host:
            return
        kwargs["hostname"] = host
        if type_str == "port":
            port_str = prompt_text("Port:", default=str(monitor.get("port", "")))
            try:
                kwargs["port"] = int(port_str) if port_str else monitor.get("port")
            except ValueError:
                kwargs["port"] = monitor.get("port")

    interval_str = prompt_text("Interval (s):", default=str(monitor.get("interval", 60)))
    try:
        kwargs["interval"] = int(interval_str) if interval_str else 60
    except ValueError:
        pass

    retries_str = prompt_text("Max retries:", default=str(monitor.get("maxretries", 0)))
    try:
        kwargs["maxretries"] = int(retries_str) if retries_str else 0
    except ValueError:
        pass

    api = _connect()
    if not api:
        return
    try:
        api.edit_monitor(monitor_id, **kwargs)
        log_action("Kuma Edit Monitor", name)
        success(f"Updated: {name}")
    except Exception as e:
        error(f"Failed to update monitor: {e}")
    finally:
        _disconnect(api)


def _delete_monitor(monitor_id, name):
    """Delete a monitor with confirmation."""
    if not confirm(f"Delete monitor '{name}'? This cannot be undone.", default_yes=False):
        return
    api = _connect()
    if not api:
        return
    try:
        api.delete_monitor(monitor_id)
        log_action("Kuma Delete Monitor", name)
        success(f"Deleted: {name}")
    except Exception as e:
        error(f"Failed to delete monitor: {e}")
    finally:
        _disconnect(api)


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------

def _bulk_pause_resume():
    """Multi-select monitors to pause or resume."""
    op_idx = pick_option("Bulk operation:", [
        "Pause monitors",
        "Resume monitors",
        "← Back",
    ])
    if op_idx == 2:
        return

    api = _connect()
    if not api:
        return
    try:
        monitors = _get_monitors_with_status(api)
    except Exception as e:
        error(f"Could not fetch monitors: {e}")
        return
    finally:
        _disconnect(api)

    if op_idx == 0:
        # Pause: show active monitors
        candidates = [m for m in monitors if m.get("active")]
        action = "Pause"
    else:
        # Resume: show paused monitors
        candidates = [m for m in monitors if not m.get("active")]
        action = "Resume"

    if not candidates:
        warn(f"No monitors available to {action.lower()}.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    choices = []
    for m in candidates:
        icon, status_str = _status_icon(m)
        name = m.get("name", "?")
        choices.append(f"{icon} {name:<30} {status_str}")

    selected = pick_multi(f"Select monitors to {action.lower()}:", choices)
    if not selected:
        return

    names = [candidates[i].get("name", "?") for i in selected]
    if not confirm(f"{action} {len(selected)} monitor(s)?"):
        return

    api2 = _connect()
    if not api2:
        return
    try:
        for i, idx in enumerate(selected):
            mid = candidates[idx].get("id")
            n = candidates[idx].get("name", "?")
            info(f"{action}ing {n}... ({i + 1}/{len(selected)})")
            if op_idx == 0:
                api2.pause_monitor(mid)
            else:
                api2.resume_monitor(mid)
        log_action(f"Kuma Bulk {action}", f"{len(selected)} monitors: {', '.join(names[:5])}")
        success(f"{action}d {len(selected)} monitors.")
    except Exception as e:
        error(f"Bulk operation failed: {e}")
    finally:
        _disconnect(api2)


# ---------------------------------------------------------------------------
# Maintenance windows
# ---------------------------------------------------------------------------

def _maintenance_menu():
    while True:
        idx = pick_option("Maintenance Windows:", [
            "Active Maintenance   — currently active windows",
            "All Maintenance      — list all maintenance windows",
            "Create Maintenance   — schedule a new window",
            "← Back",
        ])
        if idx == 3:
            return
        elif idx == 0:
            _list_maintenances(active_only=True)
        elif idx == 1:
            _list_maintenances()
        elif idx == 2:
            _create_maintenance()


def _list_maintenances(active_only=False):
    api = _connect()
    if not api:
        return
    try:
        maintenances = api.get_maintenances()
    except Exception as e:
        error(f"Could not fetch maintenance windows: {e}")
        return
    finally:
        _disconnect(api)

    if active_only:
        maintenances = [m for m in maintenances if m.get("active")]

    if not maintenances:
        msg = "No active maintenance windows." if active_only else "No maintenance windows found."
        info(msg)
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    title = "Active Maintenance" if active_only else "All Maintenance"
    choices = []
    for m in maintenances:
        is_active = m.get("active", False)
        dot = f"{C.GREEN}●{C.RESET}" if is_active else f"{C.DIM}○{C.RESET}"
        mt_title = m.get("title", "?")[:35]
        strategy = m.get("strategy", "?")
        if isinstance(strategy, str):
            strat_str = strategy
        else:
            strat_str = str(getattr(strategy, "value", strategy))
        status_str = "active" if is_active else "inactive"
        choices.append(f"{dot} {mt_title:<35} [{strat_str}] {status_str}")

    choices.append("← Back")
    idx = pick_option(title, choices)
    if idx < len(maintenances):
        _maintenance_detail(maintenances[idx].get("id"))


def _maintenance_detail(maintenance_id):
    api = _connect()
    if not api:
        return
    try:
        maint = api.get_maintenance(maintenance_id)
    except Exception as e:
        error(f"Could not fetch maintenance: {e}")
        return
    finally:
        _disconnect(api)

    title = maint.get("title", "?")
    desc = maint.get("description", "")
    is_active = maint.get("active", False)
    strategy = maint.get("strategy", "?")
    if not isinstance(strategy, str):
        strategy = str(getattr(strategy, "value", strategy))

    print(f"\n  {C.BOLD}{title}{C.RESET}")
    print(f"  Strategy: {strategy}")
    print(f"  Status: {'Active' if is_active else 'Inactive'}")
    if desc:
        print(f"  Description: {desc}")

    date_range = maint.get("dateRange", [])
    if date_range:
        print(f"  Date range: {' to '.join(str(d) for d in date_range[:2])}")
    time_range = maint.get("timeRange", [])
    if time_range:
        print(f"  Time range: {' to '.join(str(t) for t in time_range[:2])}")

    toggle = "Pause" if is_active else "Resume"
    aidx = pick_option(f"{title}:", [toggle, "Delete", "← Back"])
    if aidx == 2:
        return
    elif aidx == 1:
        if confirm(f"Delete maintenance '{title}'?", default_yes=False):
            api2 = _connect()
            if not api2:
                return
            try:
                api2.delete_maintenance(maintenance_id)
                log_action("Kuma Delete Maintenance", title)
                success(f"Deleted: {title}")
            except Exception as e:
                error(f"Failed: {e}")
            finally:
                _disconnect(api2)
    elif aidx == 0:
        api2 = _connect()
        if not api2:
            return
        try:
            if is_active:
                api2.pause_maintenance(maintenance_id)
                log_action("Kuma Pause Maintenance", title)
                success(f"Paused: {title}")
            else:
                api2.resume_maintenance(maintenance_id)
                log_action("Kuma Resume Maintenance", title)
                success(f"Resumed: {title}")
        except Exception as e:
            error(f"Failed: {e}")
        finally:
            _disconnect(api2)


def _create_maintenance():
    title = prompt_text("Maintenance title:")
    if not title:
        return

    strat_idx = pick_option("Strategy:", [
        "Manual       — toggle on/off manually",
        "Single       — one-time scheduled window",
        "← Cancel",
    ])
    if strat_idx == 2:
        return

    from uptime_kuma_api import MaintenanceStrategy
    strategy = MaintenanceStrategy.MANUAL if strat_idx == 0 else MaintenanceStrategy.SINGLE

    kwargs = {"title": title, "strategy": strategy}

    if strat_idx == 1:
        start = prompt_text("Start (YYYY-MM-DD HH:MM):")
        if not start:
            return
        end = prompt_text("End (YYYY-MM-DD HH:MM):")
        if not end:
            return
        kwargs["dateRange"] = [start, end]

    desc = prompt_text("Description (optional):", default="")
    if desc:
        kwargs["description"] = desc

    # Assign monitors
    api = _connect()
    if not api:
        return
    try:
        monitors = api.get_monitors()
        api.disconnect()
    except Exception as e:
        error(f"Could not fetch monitors: {e}")
        return

    if monitors:
        mon_choices = [m.get("name", f"#{m.get('id')}") for m in monitors]
        selected = pick_multi("Assign monitors to this maintenance:", mon_choices)
        monitor_ids = [{"id": monitors[i].get("id")} for i in selected] if selected else []
    else:
        monitor_ids = []

    api2 = _connect()
    if not api2:
        return
    try:
        result = api2.add_maintenance(**kwargs)
        maint_id = result.get("id") if isinstance(result, dict) else None
        if maint_id and monitor_ids:
            api2.add_monitor_maintenance(maint_id, monitor_ids)
        log_action("Kuma Create Maintenance", title)
        success(f"Created maintenance: {title}")
    except Exception as e:
        error(f"Failed to create maintenance: {e}")
    finally:
        _disconnect(api2)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notifications_menu():
    api = _connect()
    if not api:
        return
    try:
        notifications = api.get_notifications()
    except Exception as e:
        error(f"Could not fetch notifications: {e}")
        return
    finally:
        _disconnect(api)

    if not notifications:
        info("No notification channels configured.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        for n in notifications:
            is_active = n.get("active", n.get("isDefault", False))
            dot = f"{C.GREEN}●{C.RESET}" if is_active else f"{C.DIM}○{C.RESET}"
            nname = n.get("name", "?")[:30]
            ntype = n.get("type", "?")
            choices.append(f"{dot} {nname:<30} [{ntype}]")

        choices.extend(["───────────────", "Test a Notification", "← Back"])

        idx = pick_option("Notifications:", choices)
        if idx == len(notifications) + 2:
            return
        elif idx == len(notifications):
            continue  # separator
        elif idx == len(notifications) + 1:
            _test_notification(notifications)
        elif idx < len(notifications):
            n = notifications[idx]
            print(f"\n  {C.BOLD}{n.get('name', '?')}{C.RESET}")
            print(f"  Type: {n.get('type', '?')}")
            print(f"  Active: {'Yes' if n.get('active') else 'No'}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _test_notification(notifications):
    choices = [n.get("name", "?") for n in notifications]
    choices.append("← Cancel")
    idx = pick_option("Test notification:", choices)
    if idx >= len(notifications):
        return

    n = notifications[idx]
    info(f"Sending test to {n.get('name', '?')}...")
    api = _connect()
    if not api:
        return
    try:
        api.test_notification(**n)
        success(f"Test sent to {n.get('name', '?')}")
    except Exception as e:
        error(f"Test failed: {e}")
    finally:
        _disconnect(api)


# ---------------------------------------------------------------------------
# Status pages
# ---------------------------------------------------------------------------

def _status_pages_menu():
    api = _connect()
    if not api:
        return
    try:
        pages = api.get_status_pages()
    except Exception as e:
        error(f"Could not fetch status pages: {e}")
        return
    finally:
        _disconnect(api)

    if not pages:
        info("No status pages configured.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        for p in pages:
            ptitle = p.get("title", "?")[:30]
            slug = p.get("slug", "?")
            published = p.get("published", False)
            dot = f"{C.GREEN}●{C.RESET}" if published else f"{C.DIM}○{C.RESET}"
            choices.append(f"{dot} {ptitle:<30} /{slug}")

        choices.extend(["───────────────", "Post Incident", "Unpin Incident", "← Back"])

        idx = pick_option("Status Pages:", choices)
        if idx == len(pages) + 3:
            return
        elif idx == len(pages):
            continue  # separator
        elif idx == len(pages) + 1:
            _post_incident(pages)
        elif idx == len(pages) + 2:
            _unpin_incident(pages)
        elif idx < len(pages):
            p = pages[idx]
            print(f"\n  {C.BOLD}{p.get('title', '?')}{C.RESET}")
            print(f"  Slug: /{p.get('slug', '?')}")
            print(f"  Published: {'Yes' if p.get('published') else 'No'}")
            desc = p.get("description", "")
            if desc:
                print(f"  Description: {desc}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _post_incident(pages):
    choices = [p.get("title", "?") for p in pages]
    choices.append("← Cancel")
    idx = pick_option("Post incident to:", choices)
    if idx >= len(pages):
        return

    slug = pages[idx].get("slug")
    title = prompt_text("Incident title:")
    if not title:
        return
    content = prompt_text("Incident description:")
    if not content:
        return

    style_idx = pick_option("Incident style:", [
        "info", "warning", "danger", "primary", "← Cancel",
    ])
    if style_idx == 4:
        return
    styles = ["info", "warning", "danger", "primary"]
    style = styles[style_idx]

    api = _connect()
    if not api:
        return
    try:
        api.post_incident(slug, title=title, content=content, style=style)
        log_action("Kuma Post Incident", f"{title} on /{slug}")
        success(f"Posted incident: {title}")
    except Exception as e:
        error(f"Failed to post incident: {e}")
    finally:
        _disconnect(api)


def _unpin_incident(pages):
    choices = [p.get("title", "?") for p in pages]
    choices.append("← Cancel")
    idx = pick_option("Unpin incident from:", choices)
    if idx >= len(pages):
        return

    slug = pages[idx].get("slug")
    page_title = pages[idx].get("title", "?")
    if not confirm(f"Unpin incident from '{page_title}'?"):
        return

    api = _connect()
    if not api:
        return
    try:
        api.unpin_incident(slug)
        log_action("Kuma Unpin Incident", f"/{slug}")
        success(f"Unpinned incident from {page_title}")
    except Exception as e:
        error(f"Failed to unpin incident: {e}")
    finally:
        _disconnect(api)


# ---------------------------------------------------------------------------
# Server info
# ---------------------------------------------------------------------------

def _server_info():
    api = _connect()
    if not api:
        return
    try:
        server_info = api.info()
        try:
            db_size = api.get_database_size()
        except Exception:
            db_size = None
    except Exception as e:
        error(f"Could not fetch server info: {e}")
        return
    finally:
        _disconnect(api)

    lines = []
    lines.append(f"\n  {C.ACCENT}{C.BOLD}Uptime Kuma Server{C.RESET}\n")
    lines.append(f"  Version:       {server_info.get('version', 'unknown')}")
    latest = server_info.get("latestVersion", "")
    if latest and latest != server_info.get("version"):
        lines.append(f"  Latest:        {latest} {C.YELLOW}(update available){C.RESET}")
    if db_size:
        size_val = db_size.get("size") if isinstance(db_size, dict) else db_size
        if size_val and isinstance(size_val, (int, float)):
            lines.append(f"  Database:      {size_val / 1024 / 1024:.1f} MB")
        elif size_val:
            lines.append(f"  Database:      {size_val}")
    lines.append("")

    header = "\n".join(lines)
    idx = pick_option("", ["Shrink Database", "← Back"], header=header)
    if idx == 0:
        if confirm("Shrink the database? This may take a moment."):
            api2 = _connect()
            if not api2:
                return
            try:
                api2.shrink_database()
                log_action("Kuma Shrink Database", "")
                success("Database shrunk successfully.")
            except Exception as e:
                error(f"Failed: {e}")
            finally:
                _disconnect(api2)
