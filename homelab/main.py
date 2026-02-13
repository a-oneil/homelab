"""Entry point, CLI args, main menu loop, and plugin registry."""

import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from homelab.config import CFG, CONFIG_PATH, HISTORY_PATH, save_config
from homelab.ui import C, pick_option, success, warn, prompt_text
from homelab.modules.files import show_history, manage_bookmarks
from homelab.modules.auditlog import log_action
from homelab.modules.healthmonitor import refresh_health_alerts, get_health_alerts
from homelab.plugins.unraid import UnraidPlugin
from homelab.plugins.proxmox import ProxmoxPlugin
from homelab.plugins.unifi import UnifiPlugin
from homelab.plugins.opnsense import OpnsensePlugin
from homelab.plugins.homeassistant import HomeAssistantPlugin
from homelab.plugins.plex import PlexPlugin
from homelab.plugins.jellyfin import JellyfinPlugin
from homelab.plugins.sabnzbd import SabnzbdPlugin
from homelab.plugins.deluge import DelugePlugin
from homelab.plugins.uptimekuma import UptimeKumaPlugin
from homelab.plugins.npm import NpmPlugin
from homelab.plugins.tailscale import TailscalePlugin
from homelab.plugins.forgejo import ForgejoPlugin
from homelab.plugins.github import GitHubPlugin
from homelab.plugins.immich import ImmichPlugin
from homelab.plugins.syncthing import SyncthingPlugin
from homelab.plugins.sonarr import SonarrPlugin
from homelab.plugins.radarr import RadarrPlugin
from homelab.plugins.lidarr import LidarrPlugin
from homelab.plugins.speedtest import SpeedtestPlugin
from homelab.plugins.dockerhost import DockerHostPlugin
from homelab.plugins.ansible import AnsiblePlugin
from homelab.plugins.localhost import LocalhostPlugin

# ─── Plugin Registry ───────────────────────────────────────────────────────

PLUGINS = [
    UnraidPlugin(),
    ProxmoxPlugin(),
    UnifiPlugin(),
    OpnsensePlugin(),
    HomeAssistantPlugin(),
    PlexPlugin(),
    JellyfinPlugin(),
    SabnzbdPlugin(),
    DelugePlugin(),
    UptimeKumaPlugin(),
    NpmPlugin(),
    TailscalePlugin(),
    ForgejoPlugin(),
    GitHubPlugin(),
    ImmichPlugin(),
    SyncthingPlugin(),
    SonarrPlugin(),
    RadarrPlugin(),
    LidarrPlugin(),
    SpeedtestPlugin(),
    DockerHostPlugin(),
    AnsiblePlugin(),
    LocalhostPlugin(),
]


# ─── Settings ──────────────────────────────────────────────────────────────

def _edit_text_setting(key, label, hint=""):
    """Generic text setting editor."""
    current = CFG.get(key, "")
    if current:
        idx = pick_option(f"{label}: {current}", [
            "Edit value",
            "Clear value",
            "← Back",
        ])
        if idx == 2:
            return
        elif idx == 1:
            CFG[key] = ""
            save_config(CFG)
            log_action("Setting Changed", f"{key} = (cleared)")
            success(f"Cleared {label.lower()}")
            return
    prompt_msg = f"{label}"
    if hint:
        prompt_msg += f" ({hint})"
    if current:
        prompt_msg += f" [{current}]"
    prompt_msg += ":"
    val = prompt_text(prompt_msg)
    if val:
        CFG[key] = val
        save_config(CFG)
        log_action("Setting Changed", f"{key} = {val}")
        success(f"Updated {label.lower()}")


def _fav_display_name(fav, all_actions):
    """Get display name for a favorite (string key or dict)."""
    if isinstance(fav, dict):
        return fav.get("name", fav.get("id", "?"))
    for name, key in all_actions.items():
        if key == fav:
            return name
    return fav


def _manage_favorites(all_actions):
    while True:
        favs = CFG.get("favorites", [])
        print(f"\n  {C.BOLD}Pinned Favorites:{C.RESET}")
        if favs:
            for i, f in enumerate(favs):
                display = _fav_display_name(f, all_actions)
                print(f"    {i + 1}. {C.YELLOW}★{C.RESET} {display}")
        else:
            print(f"    {C.DIM}(none){C.RESET}")

        choices = ["+ Add favorite", "Remove favorite", "Reorder", "← Back"]
        idx = pick_option("", choices)

        if idx == 3:
            return
        elif idx == 0:
            fav_keys = {f for f in favs if isinstance(f, str)}
            available = [name for name, key in all_actions.items() if key not in fav_keys]
            if not available:
                warn("All actions are already favorites.")
                continue
            available.append("Cancel")
            sel = pick_option("Pin which action?", available)
            if sel < len(available) - 1:
                key = all_actions[available[sel]]
                CFG.setdefault("favorites", []).append(key)
                save_config(CFG)
                log_action("Favorite Add", available[sel])
                success(f"Pinned: {available[sel]}")
        elif idx == 1:
            if not favs:
                warn("No favorites to remove.")
                continue
            display_names = [_fav_display_name(f, all_actions) for f in favs]
            display_names.append("Cancel")
            sel = pick_option("Remove which?", display_names)
            if sel < len(favs):
                CFG["favorites"].pop(sel)
                save_config(CFG)
                log_action("Favorite Remove", display_names[sel])
                success(f"Unpinned: {display_names[sel]}")
        elif idx == 2:
            if len(favs) < 2:
                warn("Need at least 2 favorites to reorder.")
                continue
            display_names = [_fav_display_name(f, all_actions) for f in favs]
            display_names.append("Cancel")
            sel = pick_option("Move which?", display_names)
            if sel >= len(favs):
                continue
            positions = []
            for i in range(len(favs)):
                if i != sel:
                    label = _fav_display_name(favs[i], all_actions)
                    if i < sel:
                        positions.append(f"Before {label}")
                    else:
                        positions.append(f"After {label}")
            positions.append("Cancel")
            pidx = pick_option(f"Move '{display_names[sel]}' to:", positions)
            if pidx >= len(positions) - 1:
                continue
            # Map position choice back to index
            target_indices = [i for i in range(len(favs)) if i != sel]
            target = target_indices[pidx]
            if target > sel:
                target += 1  # insert after
            item = CFG["favorites"].pop(sel)
            CFG["favorites"].insert(target, item)
            save_config(CFG)
            log_action("Favorite Reorder", display_names[sel])
            success(f"Moved: {display_names[sel]}")


def _export_config():
    """Export config + encryption key as a password-protected bundle."""
    import base64
    import json as _json

    from homelab.ui import error, info
    from homelab.keychain import _KEY_PATH

    password = prompt_text("Set a password for the backup:")
    if not password:
        warn("Export cancelled.")
        return

    # Bundle config file + key file into a JSON envelope
    bundle = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            bundle["config"] = f.read()
    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            bundle["key"] = base64.b64encode(f.read()).decode("ascii")

    if not bundle:
        warn("Nothing to export.")
        return

    # Encrypt the bundle with a password-derived Fernet key
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        salt = os.urandom(16)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000)
        derived_key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        fernet = Fernet(derived_key)
        encrypted = fernet.encrypt(_json.dumps(bundle).encode())

        # Write to file: salt (16 bytes) + encrypted data
        default_path = os.path.expanduser("~/homelab_backup.enc")
        out_path = prompt_text(f"Save to [{default_path}]:") or default_path
        out_path = os.path.expanduser(out_path)

        with open(out_path, "wb") as f:
            f.write(salt + encrypted)

        success(f"Config exported to {out_path}")
        info("Keep the password safe — you'll need it to import.")
    except ImportError:
        error("cryptography package required. Install with: pip install cryptography")
    except Exception as e:
        error(f"Export failed: {e}")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _import_config():
    """Import config + encryption key from a password-protected bundle."""
    import base64
    import json as _json

    from homelab.ui import error, confirm as ui_confirm
    from homelab.keychain import _KEY_PATH

    default_path = os.path.expanduser("~/homelab_backup.enc")
    in_path = prompt_text(f"Backup file path [{default_path}]:") or default_path
    in_path = os.path.expanduser(in_path)

    if not os.path.exists(in_path):
        error(f"File not found: {in_path}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    password = prompt_text("Enter the backup password:")
    if not password:
        warn("Import cancelled.")
        return

    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        with open(in_path, "rb") as f:
            data = f.read()

        salt = data[:16]
        encrypted = data[16:]

        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000)
        derived_key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        fernet = Fernet(derived_key)

        try:
            decrypted = fernet.decrypt(encrypted)
        except InvalidToken:
            error("Wrong password or corrupted backup.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        bundle = _json.loads(decrypted)

        if not ui_confirm("This will overwrite your current config. Continue?", default_yes=False):
            warn("Import cancelled.")
            return

        if "config" in bundle:
            with open(CONFIG_PATH, "w") as f:
                f.write(bundle["config"])
        if "key" in bundle:
            key_data = base64.b64decode(bundle["key"])
            fd = os.open(_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, key_data)
            finally:
                os.close(fd)

        success("Config imported successfully. Restart homelab to apply.")
    except ImportError:
        error("cryptography package required. Install with: pip install cryptography")
    except Exception as e:
        error(f"Import failed: {e}")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def edit_settings(all_actions):
    while True:
        dry_str = "ON" if CFG.get("dry_run") else "OFF"
        notif_str = "ON" if CFG.get("notifications", True) else "OFF"
        print(f"\n  {C.BOLD}Settings{C.RESET} {C.DIM}({CONFIG_PATH}){C.RESET}\n")

        def _display(key, mask=False):
            val = CFG.get(key, "")
            if mask and val:
                return "****"
            return val or "(not set)"

        # Build settings items from core + plugin config fields
        theme_name = CFG.get("theme", "default").title()
        settings_items = [
            ("Theme", "__theme", None, False),
            ("Bookmarks", "__bookmarks", None, False),
            ("Favorites", "__favorites", None, False),
            (" ", "__spacer", None, False),
            ("Preferences", "__separator", None, False),
            ("Notifications", "__toggle_notifications", None, False),
            ("Dry Run Mode", "__toggle_dry_run", None, False),
            ("Default Download", "default_download_dir", None, False),
            ("Discord Webhook", "discord_webhook_url", None, False),
            ("Disk Warn (GB)", "disk_space_warn_gb", None, False),
            (" ", "__spacer", None, False),
            ("Backup", "__separator", None, False),
            ("Export Config", "__export_config", None, False),
            ("Import Config", "__import_config", None, False),
        ]

        # Add plugin config fields (grouped by service, sorted alphabetically)
        for plugin in sorted(PLUGINS, key=lambda p: p.name.lower()):
            fields = plugin.get_config_fields()
            if fields:
                settings_items.append((" ", "__spacer", None, False))
                settings_items.append((plugin.name, "__separator", None, False))
                for key, label, hint, is_secret in fields:
                    settings_items.append((label, key, hint, is_secret))

        settings_items.append(("← Back", "__back", None, False))

        choices = []
        for label, key, hint, is_secret in settings_items:
            if key == "__spacer":
                choices.append(" ")
            elif key == "__separator":
                choices.append(f"─────── {label} ───────")
            elif key == "__back":
                choices.append("← Back")
            elif key == "__theme":
                choices.append(f"Theme:              {theme_name}")
            elif key == "__bookmarks":
                choices.append(f"Bookmarks:          {len(CFG['bookmarks'])} saved")
            elif key == "__favorites":
                choices.append(f"Favorites:          {len(CFG.get('favorites', []))} pinned")
            elif key == "__toggle_notifications":
                choices.append(f"Notifications:      {notif_str}")
            elif key == "__toggle_dry_run":
                choices.append(f"Dry Run Mode:       {dry_str}")
            elif key == "__export_config":
                choices.append("Export Config:      save encrypted backup")
            elif key == "__import_config":
                choices.append("Import Config:      restore from backup")
            elif key == "disk_space_warn_gb":
                choices.append(f"Disk Warn (GB):     {CFG.get(key, 5)}")
            else:
                choices.append(f"{label + ':':<20}{_display(key, is_secret)}")

        idx = pick_option("Select to edit:", choices)

        label, key, hint, is_secret = settings_items[idx]
        if key == "__back":
            return
        elif key in ("__separator", "__spacer"):
            continue
        elif key == "__theme":
            from homelab.themes import pick_theme
            old_theme = CFG.get("theme", "default")
            pick_theme()
            new_theme = CFG.get("theme", "default")
            if new_theme != old_theme:
                log_action("Theme Changed", new_theme)
        elif key == "__export_config":
            _export_config()
            log_action("Config Export", "")
        elif key == "__import_config":
            _import_config()
            log_action("Config Import", "")
        elif key == "__bookmarks":
            manage_bookmarks()
        elif key == "__favorites":
            _manage_favorites(all_actions)
        elif key == "__toggle_notifications":
            CFG["notifications"] = not CFG.get("notifications", True)
            save_config(CFG)
            log_action("Setting Changed", f"notifications = {'ON' if CFG['notifications'] else 'OFF'}")
            success(f"Notifications: {'ON' if CFG['notifications'] else 'OFF'}")
        elif key == "__toggle_dry_run":
            CFG["dry_run"] = not CFG.get("dry_run", False)
            save_config(CFG)
            state = "ON" if CFG["dry_run"] else "OFF"
            log_action("Setting Changed", f"dry_run = {state}")
            success(f"Dry run mode: {state}")
            if CFG["dry_run"]:
                warn("No actual transfers or deletions will occur.")
        elif key == "disk_space_warn_gb":
            val = prompt_text(f"Disk space warning threshold (GB) [{CFG.get(key, 5)}]:")
            if val:
                try:
                    CFG[key] = int(val)
                    save_config(CFG)
                    log_action("Setting Changed", f"disk_space_warn_gb = {val}")
                    success(f"Disk warning threshold: {val} GB")
                except ValueError:
                    from homelab.ui import error
                    error("Must be a number.")
        else:
            _edit_text_setting(key, label, hint or "")


# ─── Shell Completions ─────────────────────────────────────────────────────

def install_completions():
    zsh_dir = os.path.expanduser("~/.zfunc")
    os.makedirs(zsh_dir, exist_ok=True)
    zsh_file = os.path.join(zsh_dir, "_homelab")
    with open(zsh_file, "w") as f:
        f.write("""#compdef homelab

_homelab() {
    _arguments \\
        '--install-completions[Install shell completions]' \\
        '--history[Show transfer history]' \\
        '--dashboard[Show server dashboard]' \\
        '--dry-run[Toggle dry run mode]' \\
        '--help[Show help]'
}

_homelab "$@"
""")

    bash_file = os.path.join(os.path.expanduser("~/.homelab"), "bash_completion")
    with open(bash_file, "w") as f:
        f.write("""_homelab_completions() {
    local opts="--install-completions --history --dashboard --dry-run --help"
    COMPREPLY=($(compgen -W "$opts" -- "${COMP_WORDS[COMP_CWORD]}"))
}
complete -F _homelab_completions homelab
""")

    print(f"\n  {C.GREEN}Completions installed!{C.RESET}\n")
    print(f"  {C.BOLD}For zsh:{C.RESET} add to {C.ACCENT}~/.zshrc{C.RESET}:")
    print("    fpath=(~/.zfunc $fpath)")
    print("    autoload -Uz compinit && compinit")
    print(f"\n  {C.BOLD}For bash:{C.RESET} add to {C.ACCENT}~/.bashrc{C.RESET} or {C.ACCENT}~/.bash_profile{C.RESET}:")
    print("    source ~/.homelab/bash_completion")
    print(f"\n  Then restart your shell or run: {C.ACCENT}source ~/.zshrc{C.RESET}")


# ─── Header ────────────────────────────────────────────────────────────────

_HEADER_CACHE_FILE = os.path.join(os.path.expanduser("~/.homelab"), "header_cache.json")
_HEADER_STATS_CACHE = {"alerts": [], "plugin_stats": []}
_HEADER_REFRESH_LOCK = threading.Lock()
_REFRESH_IN_PROGRESS = threading.Event()


_PLUGIN_TIMEOUT = 8   # seconds per plugin before skipping
_TOTAL_TIMEOUT = 15   # max seconds for entire header refresh


def _load_header_cache():
    """Load cached plugin stats from disk so the header is populated immediately."""
    try:
        import json
        with open(_HEADER_CACHE_FILE, "r") as f:
            data = json.load(f)
        with _HEADER_REFRESH_LOCK:
            _HEADER_STATS_CACHE["plugin_stats"] = data.get("plugin_stats", [])
    except Exception:
        pass


def _save_header_cache():
    """Persist plugin stats to disk for next launch."""
    try:
        import json
        with _HEADER_REFRESH_LOCK:
            data = {"plugin_stats": list(_HEADER_STATS_CACHE["plugin_stats"])}
        with open(_HEADER_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _collect_plugin_data(plugin):
    """Collect alerts + stats from a single plugin (runs in thread pool)."""
    from homelab.ui import suppress_output
    suppress_output(True)  # silence error()/warn() in this thread
    result = {"alerts": [], "stats": None}
    try:
        plugin_alerts = plugin.get_health_alerts()
        if plugin_alerts:
            result["alerts"] = list(plugin_alerts)
    except Exception:
        pass
    try:
        s = plugin.get_header_stats()
        if s:
            result["stats"] = s
    except Exception:
        pass
    return result


def _collect_health_alerts():
    """Collect health monitor alerts (runs in thread pool)."""
    from homelab.ui import suppress_output
    suppress_output(True)
    try:
        unraid_host = CFG.get("unraid_ssh_host", "")
        if unraid_host:
            refresh_health_alerts(host=unraid_host)
        return get_health_alerts(PLUGINS) or []
    except Exception:
        return []


def _test_ssh_hosts():
    """Test SSH connectivity to all configured hosts. Returns alert strings for failures."""
    hosts = {}  # host -> (port or None)
    # Gather hosts from any config key ending in _ssh_host
    for key, val in CFG.items():
        if key.endswith("_ssh_host") and isinstance(val, str) and val:
            port_key = key.replace("_ssh_host", "_ssh_port")
            port = CFG.get(port_key, "") or None
            hosts[val] = port
    # Gather docker_servers
    for server in CFG.get("docker_servers", []):
        h = server.get("host", "")
        if h:
            hosts[h] = server.get("port", "") or None

    alerts = []
    for host, port in hosts.items():
        try:
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
            if port:
                cmd.extend(["-p", str(port)])
            cmd.extend([host, "true"])
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                short = host.split("@")[-1] if "@" in host else host
                alerts.append(f"{C.RED}SSH:{C.RESET} {short} — run: ssh-copy-id {host}")
        except Exception:
            short = host.split("@")[-1] if "@" in host else host
            alerts.append(f"{C.RED}SSH:{C.RESET} {short} — run: ssh-copy-id {host}")
    return alerts


def _refresh_header_data():
    """Refresh all header data (health alerts + plugin stats) in background."""
    if _REFRESH_IN_PROGRESS.is_set():
        return  # Another refresh is already running
    _REFRESH_IN_PROGRESS.set()
    from homelab.ui import suppress_output
    suppress_output(True)  # silence error()/warn() in this thread
    try:
        from concurrent.futures import as_completed

        # Build into local lists, then swap — old cache stays visible until done
        alerts = []
        stats = []

        configured = [p for p in PLUGINS if p.is_configured()]
        workers = max(len(configured) + 2, 4)  # plugins + SSH test + health
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Submit SSH test and health check alongside plugins
            ssh_future = pool.submit(_test_ssh_hosts)
            health_future = pool.submit(_collect_health_alerts)
            plugin_futures = {pool.submit(_collect_plugin_data, p): p for p in configured}

            # Collect plugin results as they complete
            if plugin_futures:
                for future in as_completed(plugin_futures, timeout=_TOTAL_TIMEOUT):
                    try:
                        result = future.result(timeout=0)
                        if result["alerts"]:
                            alerts.extend(result["alerts"])
                        if result["stats"]:
                            stats.append(result["stats"])
                    except Exception:
                        pass  # Skip broken plugins

            # Health alerts after plugins
            try:
                health_alerts = health_future.result(timeout=_TOTAL_TIMEOUT)
                if health_alerts:
                    alerts.extend(health_alerts)
            except Exception:
                pass

            # SSH alerts last (slowest — tests each host)
            try:
                ssh_alerts = ssh_future.result(timeout=_TOTAL_TIMEOUT)
                if ssh_alerts:
                    alerts.extend(ssh_alerts)
            except Exception:
                pass

        # Swap in new data all at once
        with _HEADER_REFRESH_LOCK:
            _HEADER_STATS_CACHE["alerts"] = alerts
            _HEADER_STATS_CACHE["plugin_stats"] = stats
        _save_header_cache()
    except Exception:
        pass  # Never let header refresh crash the app
    finally:
        _REFRESH_IN_PROGRESS.clear()


def _schedule_header_refresh():
    """Kick off a background thread to refresh header data for next display."""
    if _REFRESH_IN_PROGRESS.is_set():
        return  # Skip if a refresh is already running
    t = threading.Thread(target=_refresh_header_data, daemon=True)
    t.start()


def get_header():
    """Return the app header using cached data only (never blocks on network)."""
    lines = [
        "",
        f"  {C.ACCENT}{C.BOLD}╔══════════════════════════════════╗{C.RESET}",
        f"  {C.ACCENT}{C.BOLD}║         H O M E L A B            ║{C.RESET}",
        f"  {C.ACCENT}{C.BOLD}╚══════════════════════════════════╝{C.RESET}",
    ]
    if CFG.get("dry_run"):
        lines.append(f"  {C.YELLOW}{C.BOLD}[DRY RUN MODE]{C.RESET}")

    with _HEADER_REFRESH_LOCK:
        alerts = list(_HEADER_STATS_CACHE["alerts"])
        plugin_stats = list(_HEADER_STATS_CACHE["plugin_stats"])

    for alert in alerts:
        lines.append(f"  {C.BOLD}!{C.RESET} {alert}")

    for stat in plugin_stats:
        lines.append(f"  {C.DIM}{stat}{C.RESET}")

    lines.append("")
    return "\n".join(lines)


# ─── Main Menu ─────────────────────────────────────────────────────────────

def _build_all_actions():
    """Build the global action map for favorites."""
    actions = {
        "Settings": "edit_settings",
        "Status Dashboard": "status_dashboard",
        "Transfers": "transfers_menu",
        "Quick Connect": "quick_connect_menu",
        "Docker Servers": "docker_servers_menu",
        "Container Updates": "check_all_container_updates",
        "Audit Log": "audit_log_menu",
        "Scheduled Tasks": "scheduler_menu",
        "Disk Usage Analyzer": "disk_usage_menu",
        "Network Latency": "latency_menu",
    }
    # Add plugin actions (file ops, services, etc.)
    for plugin in PLUGINS:
        if plugin.is_configured():
            for name, (key, func) in plugin.get_actions().items():
                actions[name] = key
    return actions


def _resolve_favorite(fav):
    """Resolve a favorite (string key or dict) to a callable."""
    if isinstance(fav, dict):
        for plugin in PLUGINS:
            if plugin.is_configured():
                func = plugin.resolve_favorite(fav)
                if func:
                    return func
        return None
    # String key
    from homelab.modules.dashboard import status_dashboard
    from homelab.modules.transferqueue import transfers_menu
    from homelab.modules.quickconnect import quick_connect_menu
    from homelab.modules.containerupdates import check_all_container_updates
    from homelab.modules.auditlog import audit_log_menu
    from homelab.modules.scheduler import scheduler_menu
    from homelab.modules.diskusage import disk_usage_menu
    from homelab.modules.latency import show_latency_matrix
    from homelab.plugins.dockerhost import docker_servers_menu

    def _resolve_latency():
        from homelab.modules.quickconnect import _gather_hosts
        hosts = _gather_hosts()
        if hosts:
            show_latency_matrix(hosts)

    func_map = {
        "show_history": show_history,
        "manage_bookmarks": manage_bookmarks,
        "edit_settings": lambda: edit_settings(_build_all_actions()),
        "status_dashboard": lambda: status_dashboard(PLUGINS),
        "transfers_menu": transfers_menu,
        "quick_connect_menu": quick_connect_menu,
        "docker_servers_menu": docker_servers_menu,
        "check_all_container_updates": check_all_container_updates,
        "audit_log_menu": audit_log_menu,
        "scheduler_menu": scheduler_menu,
        "disk_usage_menu": disk_usage_menu,
        "latency_menu": _resolve_latency,
    }
    for plugin in PLUGINS:
        if plugin.is_configured():
            for name, (pkey, func) in plugin.get_actions().items():
                func_map[pkey] = func
    return func_map.get(fav)


def build_main_menu():
    """Build main menu with favorites, grouped sections, dynamic services.

    Returns (menu_items, actions, action_keys) where action_keys maps each
    menu index to a string key for session restore (or None for separators).
    """
    all_actions = _build_all_actions()
    menu_items = []
    actions = []
    action_keys = []

    # Pinned favorites first
    favs = CFG.get("favorites", [])
    if favs:
        for fav in favs:
            display = _fav_display_name(fav, all_actions)
            menu_items.append(f"★ {display}")
            actions.append(_resolve_favorite(fav))
            action_keys.append(fav if isinstance(fav, str) else None)
        menu_items.append("───────────────")
        actions.append(None)
        action_keys.append(None)

    # Services (only configured ones) — sorted alphabetically
    service_entries = []
    for plugin in PLUGINS:
        if plugin.is_configured():
            for label, func in plugin.get_menu_items():
                key = None
                for name, (pkey, pfunc) in plugin.get_actions().items():
                    if pfunc is func:
                        key = pkey
                        break
                service_entries.append((label, func, key))
    service_entries.sort(key=lambda e: e[0].strip().lower())
    for label, func, key in service_entries:
        menu_items.append(label)
        actions.append(func)
        action_keys.append(key)

    # Tools section — sorted alphabetically
    menu_items.append("───────────────")
    actions.append(None)
    action_keys.append(None)

    from homelab.modules.dashboard import status_dashboard
    from homelab.modules.transferqueue import transfers_menu
    from homelab.modules.quickconnect import quick_connect_menu
    from homelab.modules.containerupdates import check_all_container_updates
    from homelab.modules.auditlog import audit_log_menu
    from homelab.modules.scheduler import scheduler_menu
    from homelab.modules.diskusage import disk_usage_menu
    from homelab.modules.latency import show_latency_matrix
    from homelab.plugins.dockerhost import docker_servers_menu

    def _latency_menu():
        from homelab.modules.quickconnect import _gather_hosts
        hosts = _gather_hosts()
        if not hosts:
            warn("No SSH hosts configured.")
            return
        show_latency_matrix(hosts)

    tools = [
        ("Audit Log            — actions, transfers, and search", audit_log_menu, "audit_log_menu"),
        ("Container Updates    — check Docker images for updates", check_all_container_updates, "check_all_container_updates"),
        ("Disk Usage Analyzer  — drill into disk usage on any host", disk_usage_menu, "disk_usage_menu"),
        ("Docker Servers       — manage Linux servers with Docker", docker_servers_menu, "docker_servers_menu"),
        ("Network Latency      — ping all hosts, show RTT matrix", _latency_menu, "latency_menu"),
        ("Quick Connect        — SSH into any host, manage keys", quick_connect_menu, "quick_connect_menu"),
        ("Scheduled Tasks      — recurring automated actions", scheduler_menu, "scheduler_menu"),
        ("Status Dashboard     — overview of all services", lambda: status_dashboard(PLUGINS), "status_dashboard"),
        ("Transfers            — queue and watch folders", transfers_menu, "transfers_menu"),
    ]
    for label, func, key in tools:
        menu_items.append(label)
        actions.append(func)
        action_keys.append(key)

    menu_items.append("───────────────")
    actions.append(None)
    action_keys.append(None)
    menu_items.append("Settings             — configure services and preferences")
    actions.append(lambda: edit_settings(all_actions))
    action_keys.append("edit_settings")
    menu_items.append("Quit")
    actions.append("__quit__")
    action_keys.append(None)

    return menu_items, actions, action_keys


# ─── Entry Point ───────────────────────────────────────────────────────────

def main():
    if "--install-completions" in sys.argv:
        install_completions()
        return
    if "--history" in sys.argv:
        show_history()
        return
    if "--dashboard" in sys.argv:
        from homelab.plugins.unraid import server_dashboard
        server_dashboard()
        return
    if "--dry-run" in sys.argv:
        CFG["dry_run"] = not CFG.get("dry_run", False)
        save_config(CFG)
        state = "ON" if CFG["dry_run"] else "OFF"
        print(f"  Dry run mode: {state}")
        return
    if "--help" in sys.argv:
        print(f"\n  {C.BOLD}homelab{C.RESET} — Self-Hosted Infrastructure Manager\n")
        print(f"  {C.BOLD}Usage:{C.RESET}")
        print("    homelab                      Interactive mode")
        print("    homelab --history            View transfer history")
        print("    homelab --dashboard          Server dashboard")
        print("    homelab --dry-run            Toggle dry run mode")
        print("    homelab --install-completions Install shell completions")
        print("    homelab --help               Show this help")
        print(f"\n  {C.BOLD}Config:{C.RESET} {CONFIG_PATH}")
        print(f"  {C.BOLD}History:{C.RESET} {HISTORY_PATH}\n")
        return

    # Load cached header stats from last session, then refresh in background
    _load_header_cache()
    _schedule_header_refresh()

    # Start scheduled tasks if any are configured
    from homelab.modules.scheduler import start_scheduler
    start_scheduler()

    # Session restore — offer to jump back to last visited menu item
    last_menu = CFG.get("session_last_menu", "")
    if last_menu:
        from homelab.ui import confirm
        func = _resolve_favorite(last_menu)
        if func:
            all_actions = _build_all_actions()
            display = _fav_display_name(last_menu, all_actions)
            print(get_header())
            if confirm(f"Resume where you left off? ({display})"):
                try:
                    func()
                except KeyboardInterrupt:
                    print()
                    warn("Interrupted. Returning to main menu.")

    while True:
        menu_items, actions, action_keys = build_main_menu()
        idx = pick_option(
            "What would you like to do?", menu_items,
            header=get_header(),
        )

        action = actions[idx]
        if action is None:
            continue  # separator
        if action == "__quit__":
            CFG["session_last_menu"] = ""
            save_config(CFG)
            print(f"\n  {C.ACCENT}Goodbye!{C.RESET}\n")
            os._exit(0)

        # Save last visited for session restore
        key = action_keys[idx]
        if key:
            CFG["session_last_menu"] = key
            save_config(CFG)

        try:
            action()
        except KeyboardInterrupt:
            print()
            warn("Interrupted. Returning to main menu.")

        # Pre-fetch header data in background so it's ready for next menu display
        _schedule_header_refresh()
        print()
