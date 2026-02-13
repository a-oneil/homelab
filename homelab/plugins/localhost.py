"""Localhost plugin — local file manager, system tools, Docker, and services."""

import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.request

from homelab.config import CFG
from homelab.modules.auditlog import log_action
from homelab.modules.transport import format_item
from homelab.notifications import copy_to_clipboard
from homelab.plugins import Plugin, add_plugin_favorite
from homelab.ui import (
    C, pick_option, pick_multi, confirm, prompt_text, scrollable_list,
    info, success, error, warn, clear_screen, bar_chart, sparkline,
)

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ─── Text file extensions for preview/edit detection ──────────────────────────

TEXT_EXT = (
    ".txt", ".md", ".json", ".xml", ".csv", ".log", ".yml", ".yaml",
    ".conf", ".cfg", ".ini", ".sh", ".py", ".js", ".html", ".css",
    ".nfo", ".srt", ".sub", ".env", ".toml", ".properties",
)

ARCHIVE_EXT = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".rar", ".7z")


# ─── Availability Checks ─────────────────────────────────────────────────────

def _has_docker():
    return shutil.which("docker") is not None


def _service_manager():
    if platform.system() == "Darwin":
        return "launchctl"
    if shutil.which("systemctl"):
        return "systemctl"
    return None


# ─── Header Cache ─────────────────────────────────────────────────────────────

_HEADER_CACHE = {"timestamp": 0, "cpu_pct": 0, "mem_used_gb": 0, "mem_total_gb": 0, "disk_pct": 0}
_CACHE_TTL = 300


def _fetch_header_stats():
    if not HAS_PSUTIL:
        return
    try:
        _HEADER_CACHE["cpu_pct"] = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        _HEADER_CACHE["mem_used_gb"] = mem.used / (1024 ** 3)
        _HEADER_CACHE["mem_total_gb"] = mem.total / (1024 ** 3)
        _HEADER_CACHE["disk_pct"] = psutil.disk_usage("/").percent
        _HEADER_CACHE["timestamp"] = time.time()
    except Exception:
        pass


# ─── Plugin Class ─────────────────────────────────────────────────────────────

class LocalhostPlugin(Plugin):
    name = "Localhost"
    key = "localhost"

    def is_configured(self):
        return True

    def get_config_fields(self):
        return []

    def get_header_stats(self):
        if not HAS_PSUTIL:
            return None
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_header_stats()
        cpu = _HEADER_CACHE.get("cpu_pct", 0)
        mu = _HEADER_CACHE.get("mem_used_gb", 0)
        mt = _HEADER_CACHE.get("mem_total_gb", 0)
        dp = _HEADER_CACHE.get("disk_pct", 0)
        if mt == 0:
            return None
        return f"Local: CPU {cpu:.0f}% | RAM {mu:.1f}/{mt:.1f}GB | Disk {dp:.0f}%"

    def get_health_alerts(self):
        if not HAS_PSUTIL:
            return []
        try:
            if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
                _fetch_header_stats()
            alerts = []
            cpu = _HEADER_CACHE.get("cpu_pct", 0)
            if cpu > 90:
                alerts.append(f"{C.RED}Localhost:{C.RESET} CPU at {cpu:.0f}%")
            mem = psutil.virtual_memory()
            if mem.percent > 90:
                alerts.append(f"{C.RED}Localhost:{C.RESET} RAM at {mem.percent:.0f}%")
            disk = psutil.disk_usage("/")
            if disk.percent > 90:
                alerts.append(f"{C.RED}Localhost:{C.RESET} Disk at {disk.percent:.0f}%")
            return alerts
        except Exception:
            return []

    def get_dashboard_widgets(self):
        if not HAS_PSUTIL:
            return []
        try:
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            cpu_pct = psutil.cpu_percent(interval=0.1)
            uptime_s = time.time() - psutil.boot_time()
            days = int(uptime_s // 86400)
            hours = int((uptime_s % 86400) // 3600)
            lines = [
                f"CPU: {cpu_pct:.0f}%  |  Up: {days}d {hours}h",
                (f"RAM: {bar_chart(mem.used, mem.total, width=20)}  "
                 f"{mem.used / (1024**3):.1f}/{mem.total / (1024**3):.1f} GB"),
                (f"Disk /: {bar_chart(disk.used, disk.total, width=20)}  "
                 f"{disk.used / (1024**3):.1f}/{disk.total / (1024**3):.1f} GB"),
            ]
            return [{"title": "Localhost", "lines": lines}]
        except Exception:
            return []

    def get_menu_items(self):
        return [
            ("Localhost            — localhost toolset", localhost_menu),
        ]

    def get_actions(self):
        actions = {
            "Local File Manager": ("localhost_file_manager", _manage_local),
            "Local Trash": ("localhost_trash", _manage_local_trash),
        }
        if HAS_PSUTIL:
            actions["Local System Info"] = ("localhost_system_info", _system_info)
            actions["Local Resource Monitor"] = ("localhost_resource_monitor", _resource_monitor)
            actions["Local Process Manager"] = ("localhost_processes", _process_manager)
            actions["Local Network Tools"] = ("localhost_network", _network_tools)
            actions["Local Disk Usage"] = ("localhost_disk_usage", _disk_usage_analyzer)
        if _has_docker():
            actions["Local Docker"] = ("localhost_docker", _local_docker_menu)
        if _service_manager():
            actions["Local Services"] = ("localhost_services", _service_manager_menu)
        return actions


# ─── Top-level Menu ───────────────────────────────────────────────────────────

def localhost_menu():
    while True:
        items = [
            "File Manager         — browse, search, rename, move, delete",
            "Trash                — view and restore deleted files",
            "───────────────",
        ]
        handlers = [_manage_local, _manage_local_trash, None]

        if HAS_PSUTIL:
            items.extend([
                "System Info          — CPU, memory, disk, OS details",
                "Resource Monitor     — live CPU, RAM, disk, network trends",
                "Process Manager      — view, search, kill processes",
                "Network Tools        — interfaces, connections, DNS, port check",
                "Disk Usage           — interactive space analyzer",
            ])
            handlers.extend([
                _system_info, _resource_monitor, _process_manager,
                _network_tools, _disk_usage_analyzer,
            ])
        else:
            items.append(f"{C.YELLOW}Install psutil for system tools: pip install psutil{C.RESET}")
            handlers.append(None)

        has_extra = _has_docker() or _service_manager()
        if has_extra:
            items.append("───────────────")
            handlers.append(None)

        if _has_docker():
            items.append("Docker               — local containers, images, prune")
            handlers.append(_local_docker_menu)

        if _service_manager():
            sm = _service_manager()
            label = "systemd" if sm == "systemctl" else "launchctl"
            items.append(f"Services             — {label} service manager")
            handlers.append(_service_manager_menu)

        items.extend([
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        handlers.extend([None, None, None])

        idx = pick_option("Localhost:", items)
        if idx == len(items) - 1:  # Back
            return
        elif idx == len(items) - 2:  # Favorites
            add_plugin_favorite(LocalhostPlugin())
        elif handlers[idx]:
            handlers[idx]()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _format_size(size_bytes):
    """Format byte count to human-readable string."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / (1024 ** 3):.1f}G"
    elif size_bytes >= 1_000_000:
        return f"{size_bytes / (1024 ** 2):.1f}M"
    elif size_bytes >= 1_000:
        return f"{size_bytes / 1024:.1f}K"
    return f"{size_bytes}"


def _rate_str(bps):
    """Format bytes/sec to human-readable rate string."""
    if bps >= 1_000_000:
        return f"{bps / (1024**2):.1f} MB/s"
    elif bps >= 1_000:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def _list_local_items(path):
    """List directory contents, returning (dirs, files) in same format as list_remote_items."""
    dirs = []
    files = []
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        error("Permission denied.")
        return [], []
    except OSError as e:
        error(f"Cannot read directory: {e}")
        return [], []

    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        if os.path.isdir(full):
            dirs.append((name, "", "dir"))
        else:
            size_str = _format_size(st.st_size)
            files.append((name, size_str, "file"))
    return dirs, files


def _get_trash_path():
    """Return the platform-appropriate trash directory."""
    if platform.system() == "Darwin":
        return os.path.expanduser("~/.Trash")
    return os.path.expanduser("~/.local/share/Trash/files")


def _get_editor():
    """Return the user's preferred editor."""
    return os.environ.get("EDITOR", "nano")


def _open_command():
    """Return the platform command to open files with default app."""
    sys = platform.system()
    if sys == "Darwin":
        return "open"
    elif sys == "Windows":
        return "start"
    return "xdg-open"


# ─── Folder Browser (for move/copy destination) ──────────────────────────────

def _browse_local_folder(start_path=None):
    """Browse local folders to pick a destination. Returns path or None."""
    current = start_path or os.path.expanduser("~")

    while True:
        try:
            entries = sorted(os.listdir(current))
        except PermissionError:
            error("Permission denied.")
            current = os.path.dirname(current)
            continue

        dir_names = [e for e in entries if os.path.isdir(os.path.join(current, e)) and not e.startswith(".")]

        choices = ["** Use this folder **"]
        if current != "/":
            choices.append(".. (go up)")
        for dn in dir_names:
            choices.append(f"{dn}/")
        choices.append("Enter path manually")
        choices.append("← Cancel")

        hdr = f"\n  {C.BOLD}Select destination:{C.RESET} {C.ACCENT}{current}/{C.RESET}\n"
        idx = pick_option("Select a folder:", choices, header=hdr)
        choice = choices[idx]

        if choice == "** Use this folder **":
            return current
        elif choice == "← Cancel":
            return None
        elif choice == ".. (go up)":
            current = os.path.dirname(current)
        elif choice == "Enter path manually":
            path = prompt_text("Path:")
            if path:
                path = os.path.expanduser(path)
                if os.path.isdir(path):
                    current = path
                else:
                    error("Not a valid directory.")
        else:
            current = os.path.join(current, choice.rstrip("/"))


# ─── Main File Manager Loop ──────────────────────────────────────────────────

def _manage_local(start_path=None):
    """Local file manager — browse, search, rename, move, delete."""
    current = start_path or os.path.expanduser("~")

    while True:
        dirs, files = _list_local_items(current)
        all_items = dirs + files

        choices = []
        if current != "/":
            choices.append(".. (go up)")

        for name, size, item_type in all_items:
            choices.append(format_item(name, size, item_type))
        choices.append("───────────────")
        choices.append("+ Create new folder")
        choices.append("Multi-select")
        choices.append("Batch rename")
        choices.append("← Back")

        hdr = (
            f"\n  {C.BOLD}Local Files:{C.RESET} {C.ACCENT}{current}/{C.RESET}\n"
            f"  {C.DIM}({len(dirs)} folders, {len(files)} files){C.RESET}\n"
        )
        idx = pick_option("", choices, header=hdr)
        choice = choices[idx]

        if choice == "← Back":
            return
        elif "────" in choice:
            continue
        elif choice == ".. (go up)":
            current = os.path.dirname(current)
        elif choice == "+ Create new folder":
            name = prompt_text("New folder name:")
            if not name:
                continue
            new_path = os.path.join(current, name)
            try:
                os.makedirs(new_path, exist_ok=True)
                log_action("Local Folder Create", new_path)
                success(f"Created: {new_path}")
            except OSError as e:
                error(f"Failed: {e}")
        elif choice == "Multi-select":
            _multi_select_local(current, all_items)
        elif choice == "Batch rename":
            _batch_rename_local(current)
        else:
            has_up = 1 if current != "/" else 0
            item_idx = idx - has_up
            if item_idx < 0 or item_idx >= len(all_items):
                continue
            name, size, item_type = all_items[item_idx]
            full_path = os.path.join(current, name)

            if item_type == "dir":
                action_labels = [
                    "Open folder",
                    "Upload to server",
                    "Search in folder",
                    "Search by file type",
                    "Find duplicates",
                    "Calculate size",
                    "Folder tree",
                    "Compress (tar.gz)",
                    "Multi-select",
                    "Rename", "Move", "Copy", "Delete", "Cancel",
                ]
                action = pick_option(f"Selected folder: {name}/", action_labels)
                al = action_labels[action]
                if al == "Open folder":
                    current = full_path
                elif al == "Upload to server":
                    _upload_to_server(full_path)
                elif al == "Search in folder":
                    _search_local(full_path)
                elif al == "Search by file type":
                    _search_by_type_local(full_path)
                elif al == "Find duplicates":
                    _find_duplicates_local(full_path)
                elif al == "Calculate size":
                    _folder_size_local(full_path)
                elif al == "Folder tree":
                    _folder_tree_local(full_path)
                elif al == "Compress (tar.gz)":
                    _compress_local(full_path, is_dir=True)
                elif al == "Multi-select":
                    sub_dirs, sub_files = _list_local_items(full_path)
                    _multi_select_local(full_path, sub_dirs + sub_files)
                elif al == "Rename":
                    _rename_local(current, name)
                elif al == "Move":
                    _move_local(full_path)
                elif al == "Copy":
                    _copy_local(full_path, is_dir=True)
                elif al == "Delete":
                    _delete_local(full_path, is_dir=True)
            else:
                actions = ["Open", "Preview"]
                lower = name.lower()
                if any(lower.endswith(ext) for ext in TEXT_EXT):
                    actions.append("Edit")
                if any(lower.endswith(ext) for ext in ARCHIVE_EXT):
                    actions.append("Extract")
                actions.extend([
                    "Upload to server",
                    "Checksum/Verify", "Compress (tar.gz)",
                    "Rename", "Move", "Copy", "Delete", "Cancel",
                ])

                action = pick_option(f"Selected file: {name} ({size})", actions)
                action_label = actions[action]

                if action_label == "Open":
                    _open_local(full_path)
                elif action_label == "Preview":
                    _preview_local(full_path, name)
                elif action_label == "Edit":
                    _edit_local(full_path)
                elif action_label == "Extract":
                    _extract_local(full_path, current)
                elif action_label == "Upload to server":
                    _upload_to_server(full_path)
                elif action_label == "Checksum/Verify":
                    _checksum_local(full_path)
                elif action_label == "Compress (tar.gz)":
                    _compress_local(full_path, is_dir=False)
                elif action_label == "Rename":
                    _rename_local(current, name)
                elif action_label == "Move":
                    _move_local(full_path)
                elif action_label == "Copy":
                    _copy_local(full_path, is_dir=False)
                elif action_label == "Delete":
                    _delete_local(full_path, is_dir=False)


# ─── File Operations ──────────────────────────────────────────────────────────

def _open_local(path):
    """Open a file with the default application."""
    try:
        subprocess.Popen([_open_command(), path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        success(f"Opened: {os.path.basename(path)}")
    except OSError as e:
        error(f"Failed to open: {e}")


def _rename_local(parent_path, old_name):
    """Rename a file or folder."""
    new_name = prompt_text(f"Rename '{old_name}' to:")
    if not new_name:
        warn("Cancelled.")
        return
    old = os.path.join(parent_path, old_name)
    new = os.path.join(parent_path, new_name)
    try:
        os.rename(old, new)
        log_action("Local Rename", f"{old} → {new_name}")
        success(f"Renamed to: {new_name}")
    except OSError as e:
        error(f"Failed: {e}")


def _move_local(source_path):
    """Move a file or folder to another local directory."""
    name = os.path.basename(source_path)
    info("Select destination folder:")
    dest = _browse_local_folder(os.path.dirname(source_path))
    if not dest:
        return
    dest_path = os.path.join(dest, name)
    if os.path.exists(dest_path):
        if not confirm(f"'{name}' already exists in destination. Overwrite?", default_yes=False):
            warn("Cancelled.")
            return
    try:
        shutil.move(source_path, dest_path)
        log_action("Local Move", f"{source_path} → {dest_path}")
        success(f"Moved to: {dest_path}")
    except OSError as e:
        error(f"Failed: {e}")


def _copy_local(source_path, is_dir=False):
    """Copy a file or folder to another local directory."""
    name = os.path.basename(source_path)
    info("Select destination folder:")
    dest = _browse_local_folder(os.path.dirname(source_path))
    if not dest:
        return
    dest_path = os.path.join(dest, name)
    if os.path.exists(dest_path):
        if not confirm(f"'{name}' already exists in destination. Overwrite?", default_yes=False):
            warn("Cancelled.")
            return
        if is_dir:
            shutil.rmtree(dest_path, ignore_errors=True)
    try:
        if is_dir:
            shutil.copytree(source_path, dest_path)
        else:
            shutil.copy2(source_path, dest_path)
        log_action("Local Copy", f"{source_path} → {dest_path}")
        success(f"Copied to: {dest_path}")
    except OSError as e:
        error(f"Failed: {e}")


def _delete_local(path, is_dir=False):
    """Delete a file or folder (trash or permanent)."""
    name = os.path.basename(path)
    action = pick_option(
        f"Delete '{name}'?",
        ["Move to trash", "Permanently delete", "Cancel"],
    )
    if action == 2:
        warn("Cancelled.")
        return
    elif action == 0:
        trash = _get_trash_path()
        os.makedirs(trash, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        trash_name = f"{name}_{ts}"
        trash_dest = os.path.join(trash, trash_name)
        try:
            shutil.move(path, trash_dest)
            # Save origin for restore
            with open(trash_dest + ".origin", "w") as f:
                f.write(path)
            log_action("Local Trash", path)
            success(f"Moved to trash: {name}")
        except OSError as e:
            error(f"Failed: {e}")
    else:
        if not confirm("Permanently delete? This cannot be undone.", default_yes=False):
            warn("Cancelled.")
            return
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                os.remove(path)
            log_action("Local Delete", path)
            success(f"Deleted: {name}")
        except OSError as e:
            error(f"Failed: {e}")


def _edit_local(path):
    """Open a file in the user's editor."""
    editor = _get_editor()
    subprocess.run([editor, path])


def _upload_to_server(local_path):
    """Upload a local file/folder to a configured remote server."""
    from homelab.modules.files import upload_local
    # Find configured hosts from plugins that have SSH
    hosts = []
    for key in ["unraid_ssh_host", "ansible_ssh_host"]:
        h = CFG.get(key, "")
        if h:
            hosts.append(h)
    docker_servers = CFG.get("docker_servers", [])
    for srv in docker_servers:
        h = srv.get("host", "")
        if h:
            hosts.append(h)
    if not hosts:
        warn("No remote hosts configured.")
        return
    if len(hosts) == 1:
        host = hosts[0]
    else:
        choices = hosts + ["← Cancel"]
        idx = pick_option("Upload to which host?", choices)
        if idx >= len(hosts):
            return
        host = hosts[idx]
    # Use the base path from config or /tmp
    base = CFG.get("unraid_base_path", "/tmp")
    info(f"Uploading {os.path.basename(local_path)} to {host}...")
    upload_local(host, base)


# ─── Preview / Extract / Compress / Checksum ──────────────────────────────────

def _preview_local(path, name):
    """Preview a local file based on its type."""
    lower = name.lower()

    if any(lower.endswith(ext) for ext in TEXT_EXT):
        print(f"\n  {C.BOLD}Preview: {name} (first 40 lines){C.RESET}\n")
        try:
            with open(path, "r", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= 40:
                        break
                    print(f"  {C.DIM}|{C.RESET} {line.rstrip()}")
        except OSError as e:
            error(f"Could not read file: {e}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    img_ext = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp")
    if any(lower.endswith(ext) for ext in img_ext):
        print(f"\n  {C.BOLD}Image info: {name}{C.RESET}\n")
        result = subprocess.run(["file", path], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  {result.stdout.strip()}")
        try:
            sz = os.path.getsize(path)
            print(f"  Size: {_format_size(sz)}")
        except OSError:
            pass
        result2 = subprocess.run(["identify", path], capture_output=True, text=True)
        if result2.returncode == 0 and result2.stdout.strip():
            print(f"  {result2.stdout.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    media_ext = (
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
        ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a", ".m4v",
    )
    if any(lower.endswith(ext) for ext in media_ext):
        print(f"\n  {C.BOLD}Media info: {name}{C.RESET}\n")
        result = subprocess.run(
            ["mediainfo", path], capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines()[:30]:
                print(f"  {line}")
        else:
            result = subprocess.run(
                ["ffprobe", "-hide_banner", path],
                capture_output=True, text=True, stderr=subprocess.STDOUT,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines()[:20]:
                    print(f"  {line}")
            else:
                result = subprocess.run(["file", path], capture_output=True, text=True)
                if result.returncode == 0:
                    print(f"  {result.stdout.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Generic file info
    print(f"\n  {C.BOLD}File info: {name}{C.RESET}\n")
    result = subprocess.run(["file", path], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  {result.stdout.strip()}")
    try:
        st = os.stat(path)
        print(f"  Size: {_format_size(st.st_size)}")
        mod = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  Modified: {mod}")
    except OSError:
        pass
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _extract_local(archive_path, current_dir):
    """Extract an archive locally."""
    name = os.path.basename(archive_path)
    lower = name.lower()

    extract_to = prompt_text(f"Extract to [{current_dir}]:") or current_dir
    extract_to = os.path.expanduser(extract_to)

    if not os.path.isdir(extract_to):
        try:
            os.makedirs(extract_to, exist_ok=True)
        except OSError as e:
            error(f"Cannot create directory: {e}")
            return

    info(f"Extracting {name}...")

    if lower.endswith(".zip"):
        cmd = ["unzip", "-o", archive_path, "-d", extract_to]
    elif lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        cmd = ["tar", "-xzf", archive_path, "-C", extract_to]
    elif lower.endswith(".tar.bz2"):
        cmd = ["tar", "-xjf", archive_path, "-C", extract_to]
    elif lower.endswith(".tar"):
        cmd = ["tar", "-xf", archive_path, "-C", extract_to]
    elif lower.endswith(".rar"):
        cmd = ["unrar", "x", archive_path, extract_to + "/"]
    elif lower.endswith(".7z"):
        cmd = ["7z", "x", archive_path, f"-o{extract_to}"]
    else:
        error("Unsupported archive format.")
        return

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error(f"Extraction failed: {result.stderr.strip()}")
    else:
        log_action("Local Extract", f"{archive_path} → {extract_to}")
        success(f"Extracted to: {extract_to}")


def _compress_local(path, is_dir=False):
    """Compress a file or folder as tar.gz."""
    name = os.path.basename(path)
    parent = os.path.dirname(path)
    default_name = f"{name}.tar.gz"
    archive_name = prompt_text(f"Archive name [{default_name}]:") or default_name
    if not archive_name.endswith((".tar.gz", ".tgz")):
        archive_name += ".tar.gz"
    archive_path = os.path.join(parent, archive_name)

    info(f"Compressing {name}...")
    result = subprocess.run(
        ["tar", "-czf", archive_path, "-C", parent, name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        error(f"Compression failed: {result.stderr.strip()}")
    else:
        try:
            sz = os.path.getsize(archive_path)
            success(f"Created: {archive_name} ({_format_size(sz)})")
        except OSError:
            success(f"Created: {archive_name}")
        log_action("Local Compress", f"{path} → {archive_name}")


def _checksum_local(path):
    """Calculate or verify file checksums."""
    name = os.path.basename(path)
    idx = pick_option(f"Checksum for {name}:", [
        "MD5", "SHA256", "Both", "Verify against hash", "Cancel",
    ])
    if idx == 4:
        return
    print()
    hashes = {}

    if idx in (0, 2):
        info("Computing MD5...")
        h = hashlib.md5()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            md5 = h.hexdigest()
            hashes["MD5"] = md5
            print(f"  {C.BOLD}MD5:{C.RESET}    {C.ACCENT}{md5}{C.RESET}")
        except OSError as e:
            error(f"MD5 failed: {e}")

    if idx in (1, 2):
        info("Computing SHA256...")
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            sha = h.hexdigest()
            hashes["SHA256"] = sha
            print(f"  {C.BOLD}SHA256:{C.RESET} {C.ACCENT}{sha}{C.RESET}")
        except OSError as e:
            error(f"SHA256 failed: {e}")

    if idx == 3:
        expected = prompt_text("Enter expected hash (MD5 or SHA256):").strip().lower()
        if not expected:
            return
        if len(expected) == 32:
            algo, label = hashlib.md5, "MD5"
        elif len(expected) == 64:
            algo, label = hashlib.sha256, "SHA256"
        else:
            error("Invalid hash length. MD5=32 chars, SHA256=64 chars.")
            input("\n  Press Enter to continue...")
            return
        info(f"Computing {label}...")
        h = algo()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            actual = h.hexdigest()
            if actual == expected:
                success(f"{label} verification: MATCH")
            else:
                error(f"{label} verification: MISMATCH")
                print(f"  Expected: {expected}")
                print(f"  Actual:   {actual}")
        except OSError as e:
            error(f"Checksum failed: {e}")

    if hashes:
        print()
        if confirm("Copy hash to clipboard?", default_yes=False):
            to_copy = hashes.get("SHA256") or hashes.get("MD5", "")
            if copy_to_clipboard(to_copy):
                success("Copied to clipboard.")
            else:
                error("Copy failed.")
    print()
    input("  Press Enter to continue...")


# ─── Search ───────────────────────────────────────────────────────────────────

def _search_local(folder_path):
    """Search by filename or content within a local folder."""
    mode = pick_option("Search mode:", ["By filename", "By content (grep)", "Cancel"])
    if mode == 2:
        return

    pattern = prompt_text("Search pattern:")
    if not pattern:
        warn("No pattern entered.")
        return

    info(f"Searching in {folder_path}...")

    if mode == 0:
        result = subprocess.run(
            ["find", folder_path, "-iname", f"*{pattern}*"],
            capture_output=True, text=True, timeout=30,
        )
    else:
        result = subprocess.run(
            ["grep", "-r", "-l", "-i", pattern, folder_path],
            capture_output=True, text=True, timeout=30,
        )

    if result.returncode != 0 or not result.stdout.strip():
        warn("No results found.")
        return

    matches = [ln.strip() for ln in result.stdout.strip().splitlines()[:50] if ln.strip()]
    success(f"Found {len(matches)} result(s):")

    choices = []
    for m in matches:
        rel = m.replace(os.path.expanduser("~"), "~", 1)
        choices.append(rel)
    choices.append("← Back")

    idx = pick_option("Select a result:", choices)
    if idx >= len(matches):
        return

    selected = matches[idx]
    if os.path.isdir(selected):
        action = pick_option(f"Selected: {selected}", ["Open in file manager", "Cancel"])
        if action == 0:
            _manage_local(selected)
    else:
        action = pick_option(
            f"Selected: {os.path.basename(selected)}",
            ["Open", "Preview", "Cancel"],
        )
        if action == 0:
            _open_local(selected)
        elif action == 1:
            _preview_local(selected, os.path.basename(selected))


def _search_by_type_local(folder_path):
    """Search for files by extension within a local folder."""
    ext = prompt_text("File extension (e.g. .mkv, .jpg):")
    if not ext:
        return
    if not ext.startswith("."):
        ext = "." + ext

    info(f"Searching for *{ext} in {folder_path}...")
    result = subprocess.run(
        ["find", folder_path, "-iname", f"*{ext}"],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0 or not result.stdout.strip():
        warn("No results found.")
        return

    matches = [ln.strip() for ln in result.stdout.strip().splitlines()[:50] if ln.strip()]
    success(f"Found {len(matches)} result(s):")

    choices = []
    for m in matches:
        rel = m.replace(os.path.expanduser("~"), "~", 1)
        choices.append(rel)
    choices.append("← Back")

    idx = pick_option("Select a result:", choices)
    if idx >= len(matches):
        return

    selected = matches[idx]
    action = pick_option(
        f"Selected: {os.path.basename(selected)}",
        ["Open", "Preview", "Cancel"],
    )
    if action == 0:
        _open_local(selected)
    elif action == 1:
        _preview_local(selected, os.path.basename(selected))


# ─── Folder Tools ─────────────────────────────────────────────────────────────

def _folder_size_local(path):
    """Calculate and display the size of a local folder."""
    name = os.path.basename(path)
    info(f"Calculating size of {name}...")
    result = subprocess.run(["du", "-sh", path], capture_output=True, text=True)
    if result.returncode != 0:
        error(f"Failed: {result.stderr.strip()}")
    else:
        size = result.stdout.strip().split("\t")[0]
        success(f"{name}: {size}")
    input("\n  Press Enter to continue...")


def _folder_tree_local(path):
    """Display a tree view of a local folder."""
    name = os.path.basename(path)
    result = subprocess.run(
        ["tree", "-a", "--dirsfirst", "-L", "4", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        result = subprocess.run(
            ["find", path, "-maxdepth", "4"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            error("Could not generate folder tree.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return
        # Format find output as tree-like
        lines = sorted(result.stdout.strip().splitlines())
        clear_screen()
        print(f"\n  {C.BOLD}Tree: {name}/{C.RESET}\n")
        for line in lines:
            rel = line.replace(path, "", 1)
            if rel:
                depth = rel.count("/")
                indent = "  " * depth
                print(f"  {indent}{os.path.basename(rel)}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    clear_screen()
    print(f"\n  {C.BOLD}Tree: {name}/{C.RESET}\n")
    for line in result.stdout.strip().splitlines():
        print(f"  {line}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _find_duplicates_local(scan_path):
    """Scan a local path for duplicate files by size + partial hash."""
    info("Scanning for files with identical sizes...")

    size_groups = {}
    try:
        for root, _dirs, fnames in os.walk(scan_path):
            for fn in fnames:
                fp = os.path.join(root, fn)
                try:
                    sz = os.path.getsize(fp)
                    if sz > 0:
                        size_groups.setdefault(sz, []).append(fp)
                except OSError:
                    continue
    except OSError:
        error("Scan failed.")
        return

    candidates = {s: paths for s, paths in size_groups.items() if len(paths) > 1}

    if not candidates:
        info("No duplicate candidates found.")
        return

    info(f"Found {len(candidates)} size group(s) with potential duplicates.")
    info("Checking partial hashes...")

    dup_groups = []
    for size, paths in candidates.items():
        hash_map = {}
        for p in paths:
            try:
                h = hashlib.md5()
                with open(p, "rb") as f:
                    h.update(f.read(4096))
                digest = h.hexdigest()
                hash_map.setdefault(digest, []).append(p)
            except OSError:
                continue
        for digest, group in hash_map.items():
            if len(group) > 1:
                dup_groups.append((size, group))

    if not dup_groups:
        success("No duplicates found!")
        return

    total = sum(len(g) - 1 for _, g in dup_groups)
    print(f"\n  {C.BOLD}Found {len(dup_groups)} duplicate group(s) "
          f"({total} extra files):{C.RESET}")

    for size, group in dup_groups:
        sz_mb = size / 1_000_000
        print(f"\n  {C.ACCENT}Size: {sz_mb:.1f} MB{C.RESET}")
        for i, p in enumerate(group):
            marker = " (keep)" if i == 0 else " (duplicate)"
            print(f"    {i + 1}. {p}{C.DIM}{marker}{C.RESET}")

        action = pick_option(
            "Action for this group:",
            ["Delete duplicates (keep first)", "Skip", "Stop"],
        )
        if action == 0:
            for p in group[1:]:
                try:
                    os.remove(p)
                    success(f"Deleted: {os.path.basename(p)}")
                except OSError as e:
                    error(f"Failed: {p} — {e}")
        elif action == 2:
            return

    success("Duplicate scan complete.")


# ─── Multi-select / Batch ─────────────────────────────────────────────────────

def _multi_select_local(current_dir, all_items):
    """Multi-select files/folders for batch operations."""
    if not all_items:
        info("No items to select.")
        return
    options = [format_item(n, s, t) for n, s, t in all_items]
    hdr = (
        f"\n  {C.BOLD}Multi-Select:{C.RESET} {C.ACCENT}{current_dir}/{C.RESET}\n"
        f"  {C.DIM}Use Space to toggle selection{C.RESET}\n"
    )
    selected = pick_multi("Select items:", options, header=hdr)
    if not selected:
        info("No items selected.")
        return
    items = [all_items[i] for i in selected]
    action_idx = pick_option(f"Selected {len(items)} item(s):", [
        "Delete all (to trash)", "Move all",
        "Compress all (tar.gz)", "Cancel",
    ])
    if action_idx == 3:
        return
    label = ["Delete", "Move", "Compress"][action_idx]
    if not confirm(f"{label} {len(items)} item(s)?"):
        warn("Cancelled.")
        return
    ok = 0
    if action_idx == 0:  # Delete to trash
        trash = _get_trash_path()
        os.makedirs(trash, exist_ok=True)
        for name, size, item_type in items:
            full = os.path.join(current_dir, name)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            trash_name = f"{name}_{ts}"
            try:
                shutil.move(full, os.path.join(trash, trash_name))
                with open(os.path.join(trash, trash_name + ".origin"), "w") as f:
                    f.write(full)
                ok += 1
            except OSError:
                error(f"Failed: {name}")
    elif action_idx == 1:  # Move
        info("Select destination folder:")
        dest = _browse_local_folder(current_dir)
        if not dest:
            return
        for name, size, item_type in items:
            full = os.path.join(current_dir, name)
            try:
                shutil.move(full, os.path.join(dest, name))
                ok += 1
            except OSError:
                error(f"Failed: {name}")
    elif action_idx == 2:  # Compress all into one archive
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"batch_{ts}.tar.gz"
        names = [item[0] for item in items]
        info(f"Compressing {len(items)} items...")
        result = subprocess.run(
            ["tar", "-czf", archive_name] + names,
            capture_output=True, text=True, cwd=current_dir,
        )
        if result.returncode == 0:
            success(f"Created: {archive_name}")
            return
        else:
            error(f"Compression failed: {result.stderr.strip()}")
            return
    success(f"Completed: {ok}/{len(items)} successful")


def _batch_rename_local(current_dir):
    """Regex-based batch rename of files in a local directory."""
    pattern = prompt_text("Find pattern (regex):")
    if not pattern:
        return
    replacement = prompt_text("Replace with:")
    if replacement is None:
        return

    try:
        entries = sorted(os.listdir(current_dir))
    except OSError:
        error("Could not list files.")
        return

    names = [n for n in entries if not n.startswith(".")]
    renames = []
    try:
        for name in names:
            new_name = re.sub(pattern, replacement, name)
            if new_name != name:
                renames.append((name, new_name))
    except re.error as e:
        error(f"Invalid regex: {e}")
        return

    if not renames:
        info("No files matched the pattern.")
        return

    print(f"\n  {C.BOLD}Preview ({len(renames)} renames):{C.RESET}")
    for old, new in renames:
        print(f"    {old}  →  {C.ACCENT}{new}{C.RESET}")

    if not confirm(f"Rename {len(renames)} file(s)?"):
        warn("Cancelled.")
        return

    ok = 0
    for old, new in renames:
        old_path = os.path.join(current_dir, old)
        new_path = os.path.join(current_dir, new)
        try:
            os.rename(old_path, new_path)
            ok += 1
        except OSError:
            error(f"Failed: {old} → {new}")
    if ok > 0:
        log_action("Local Batch Rename", f"{ok} file(s) in {current_dir}")
    success(f"Renamed {ok}/{len(renames)} files.")


# ─── Trash Management ─────────────────────────────────────────────────────────

def _manage_local_trash():
    """Browse and manage items in the local trash."""
    trash = _get_trash_path()
    while True:
        if not os.path.isdir(trash):
            info("Trash is empty.")
            return

        try:
            all_entries = sorted(os.listdir(trash))
        except OSError:
            error("Cannot read trash directory.")
            return

        items = [e for e in all_entries if not e.endswith(".origin")]
        if not items:
            info("Trash is empty.")
            return

        choices = items + ["Empty all trash", "← Back"]
        idx = pick_option("Trash:", choices)

        if choices[idx] == "← Back":
            return
        elif choices[idx] == "Empty all trash":
            if confirm("Permanently delete ALL items in trash?", default_yes=False):
                for item in all_entries:
                    full = os.path.join(trash, item)
                    try:
                        if os.path.isdir(full):
                            shutil.rmtree(full)
                        else:
                            os.remove(full)
                    except OSError:
                        pass
                log_action("Local Trash Empty", trash)
                success("Trash emptied.")
            return
        else:
            trash_item = items[idx]
            full = os.path.join(trash, trash_item)
            origin_file = full + ".origin"
            orig = ""
            if os.path.isfile(origin_file):
                try:
                    with open(origin_file) as f:
                        orig = f.read().strip()
                except OSError:
                    pass

            action = pick_option(
                f"Trash item: {trash_item}",
                [
                    f"Restore to {orig}" if orig else "Restore (original path unknown)",
                    "Permanently delete",
                    "Cancel",
                ],
            )
            if action == 0 and orig:
                dest_dir = os.path.dirname(orig)
                os.makedirs(dest_dir, exist_ok=True)
                try:
                    shutil.move(full, orig)
                    if os.path.isfile(origin_file):
                        os.remove(origin_file)
                    log_action("Local Trash Restore", orig)
                    success(f"Restored: {orig}")
                except OSError as e:
                    error(f"Failed: {e}")
            elif action == 1:
                try:
                    if os.path.isdir(full):
                        shutil.rmtree(full)
                    else:
                        os.remove(full)
                    if os.path.isfile(origin_file):
                        os.remove(origin_file)
                    log_action("Local Delete", f"{full} (from trash)")
                    success(f"Permanently deleted: {trash_item}")
                except OSError as e:
                    error(f"Failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  SYSTEM TOOLS (require psutil)
# ═════════════════════════════════════════════════════════════════════════════

# ─── System Info ──────────────────────────────────────────────────────────────

def _system_info():
    """Display local system information snapshot."""
    if not HAS_PSUTIL:
        warn("psutil is required. Install with: pip install psutil")
        return

    hostname = platform.node()
    os_info = platform.platform()
    cpu_model = platform.processor() or "?"

    # macOS: platform.processor() returns 'arm', use sysctl for full name
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True,
            )
            if r.returncode == 0 and r.stdout.strip():
                cpu_model = r.stdout.strip()
        except OSError:
            pass
    elif platform.system() == "Linux" and cpu_model in ("", "?", "x86_64", "aarch64"):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu_model = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass

    cores_phys = psutil.cpu_count(logical=False) or "?"
    cores_logical = psutil.cpu_count(logical=True) or "?"
    mem = psutil.virtual_memory()
    uptime_s = time.time() - psutil.boot_time()
    days = int(uptime_s // 86400)
    hours = int((uptime_s % 86400) // 3600)
    mins = int((uptime_s % 3600) // 60)
    py_ver = platform.python_version()
    cpu_pct = psutil.cpu_percent(interval=0.5)

    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USER", "?")

    print(f"\n  {C.BOLD}Localhost — System Info{C.RESET}\n")
    print(f"  Hostname:  {hostname}")
    print(f"  User:      {user}")
    print(f"  OS:        {os_info}")
    print(f"  CPU:       {cpu_model}")
    print(f"  Cores:     {cores_phys} physical / {cores_logical} logical")
    print(f"  CPU Usage: {cpu_pct}%")
    print(f"  Uptime:    {days}d {hours}h {mins}m")
    print(f"  Python:    {py_ver}")
    print()

    # Memory
    mem_gb_used = mem.used / (1024 ** 3)
    mem_gb_total = mem.total / (1024 ** 3)
    print(f"  Memory:    {bar_chart(mem.used, mem.total, width=25)}  "
          f"{mem_gb_used:.1f} / {mem_gb_total:.1f} GB")

    # Swap
    swap = psutil.swap_memory()
    if swap.total > 0:
        swap_gb_used = swap.used / (1024 ** 3)
        swap_gb_total = swap.total / (1024 ** 3)
        print(f"  Swap:      {bar_chart(swap.used, swap.total, width=25)}  "
              f"{swap_gb_used:.1f} / {swap_gb_total:.1f} GB")

    print()

    # Disks
    for part in psutil.disk_partitions():
        if "snap" in part.mountpoint or "loop" in part.device:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        gb_used = usage.used / (1024 ** 3)
        gb_total = usage.total / (1024 ** 3)
        mp = part.mountpoint
        if len(mp) > 20:
            mp = "..." + mp[-17:]
        print(f"  {mp:<20} {bar_chart(usage.used, usage.total, width=25)}  "
              f"{gb_used:.1f} / {gb_total:.1f} GB")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Resource Monitor ─────────────────────────────────────────────────────────

def _resource_monitor():
    """Live resource monitoring with sparkline trends."""
    if not HAS_PSUTIL:
        warn("psutil is required. Install with: pip install psutil")
        return

    samples = 10
    interval = 1

    cpu_hist = []
    mem_hist = []
    disk_read_hist = []
    disk_write_hist = []
    net_sent_hist = []
    net_recv_hist = []

    print()

    # Prime the measurements
    prev_disk = psutil.disk_io_counters()
    prev_net = psutil.net_io_counters()
    psutil.cpu_percent()

    for i in range(samples):
        time.sleep(interval)
        print(f"\r  Collecting sample {i + 1}/{samples}...", end="", flush=True)

        cpu_hist.append(psutil.cpu_percent())
        mem_hist.append(psutil.virtual_memory().percent)

        cur_disk = psutil.disk_io_counters()
        cur_net = psutil.net_io_counters()

        if cur_disk and prev_disk:
            disk_read_hist.append((cur_disk.read_bytes - prev_disk.read_bytes) / interval)
            disk_write_hist.append((cur_disk.write_bytes - prev_disk.write_bytes) / interval)
            prev_disk = cur_disk
        else:
            disk_read_hist.append(0)
            disk_write_hist.append(0)

        if cur_net and prev_net:
            net_sent_hist.append((cur_net.bytes_sent - prev_net.bytes_sent) / interval)
            net_recv_hist.append((cur_net.bytes_recv - prev_net.bytes_recv) / interval)
            prev_net = cur_net
        else:
            net_sent_hist.append(0)
            net_recv_hist.append(0)

    # Clear progress line
    print("\r" + " " * 40 + "\r", end="")

    clear_screen()
    print(f"\n  {C.BOLD}Localhost — Resource Monitor{C.RESET}  "
          f"{C.DIM}({samples} samples, {interval}s interval){C.RESET}\n")

    # CPU
    last_cpu = cpu_hist[-1]
    cpu_color = C.RED if last_cpu > 80 else (C.YELLOW if last_cpu > 50 else C.GREEN)
    print(f"  {'CPU':12} {cpu_color}{sparkline(cpu_hist, width=samples)}{C.RESET}  "
          f"{last_cpu:>5.1f}%")

    # Memory
    last_mem = mem_hist[-1]
    mem_color = C.RED if last_mem > 80 else (C.YELLOW if last_mem > 50 else C.GREEN)
    print(f"  {'Memory':12} {mem_color}{sparkline(mem_hist, width=samples)}{C.RESET}  "
          f"{last_mem:>5.1f}%")

    # Disk I/O
    print(f"  {'Disk Read':12} {C.ACCENT}{sparkline(disk_read_hist, width=samples)}{C.RESET}  "
          f"{_rate_str(disk_read_hist[-1]):>10}")
    print(f"  {'Disk Write':12} {C.ACCENT}{sparkline(disk_write_hist, width=samples)}{C.RESET}  "
          f"{_rate_str(disk_write_hist[-1]):>10}")

    # Network I/O
    print(f"  {'Net Sent':12} {C.ACCENT}{sparkline(net_sent_hist, width=samples)}{C.RESET}  "
          f"{_rate_str(net_sent_hist[-1]):>10}")
    print(f"  {'Net Recv':12} {C.ACCENT}{sparkline(net_recv_hist, width=samples)}{C.RESET}  "
          f"{_rate_str(net_recv_hist[-1]):>10}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Process Manager ──────────────────────────────────────────────────────────

def _process_manager():
    """Local process manager using psutil."""
    if not HAS_PSUTIL:
        warn("psutil is required. Install with: pip install psutil")
        return
    while True:
        idx = pick_option("Process Manager:", [
            "Top by CPU        — highest CPU usage",
            "Top by Memory     — highest memory usage",
            "Search by name    — find processes by name",
            "Kill a process    — send signal by PID",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 0:
            _list_local_processes("cpu")
        elif idx == 1:
            _list_local_processes("memory")
        elif idx == 2:
            _search_processes()
        elif idx == 3:
            _kill_local_process()


def _list_local_processes(sort_by):
    """List top processes sorted by CPU or memory."""
    # Trigger CPU measurement
    psutil.cpu_percent()
    time.sleep(0.5)

    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "username"]):
        try:
            pi = p.info
            procs.append(pi)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    procs.sort(key=lambda x: x.get(key) or 0, reverse=True)
    procs = procs[:50]

    rows = []
    for p in procs:
        pid = p.get("pid", "?")
        name = (p.get("name") or "?")[:30]
        cpu = p.get("cpu_percent") or 0
        mem = p.get("memory_percent") or 0
        user = (p.get("username") or "?")[:15]

        if cpu > 50:
            cpu_str = f"{C.RED}{cpu:>5.1f}%{C.RESET}"
        elif cpu > 20:
            cpu_str = f"{C.YELLOW}{cpu:>5.1f}%{C.RESET}"
        else:
            cpu_str = f"{C.GREEN}{cpu:>5.1f}%{C.RESET}"

        if mem > 50:
            mem_str = f"{C.RED}{mem:>5.1f}%{C.RESET}"
        elif mem > 20:
            mem_str = f"{C.YELLOW}{mem:>5.1f}%{C.RESET}"
        else:
            mem_str = f"{mem:>5.1f}%"

        rows.append(f"  {pid:<8} {name:<30} {cpu_str}  {mem_str}  {user}")

    label = "CPU" if sort_by == "cpu" else "Memory"
    header_line = f"  {'PID':<8} {'NAME':<30} {'CPU':>6}  {'MEM':>6}  {'USER'}"
    scrollable_list(
        f"Top by {label} ({len(procs)} processes)",
        rows,
        header_line=header_line,
    )


def _search_processes():
    """Search processes by name."""
    pattern = prompt_text("Process name to search:")
    if not pattern:
        return
    pattern_lower = pattern.lower()

    matches = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "username"]):
        try:
            pi = p.info
            if pattern_lower in (pi.get("name") or "").lower():
                matches.append(pi)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not matches:
        warn(f"No processes matching '{pattern}'.")
        return

    rows = []
    for p in matches:
        pid = p.get("pid", "?")
        name = (p.get("name") or "?")[:30]
        cpu = p.get("cpu_percent") or 0
        mem = p.get("memory_percent") or 0
        user = (p.get("username") or "?")[:15]
        rows.append(f"  {pid:<8} {name:<30} {cpu:>5.1f}%  {mem:>5.1f}%  {user}")

    header_line = f"  {'PID':<8} {'NAME':<30} {'CPU':>6}  {'MEM':>6}  {'USER'}"
    scrollable_list(
        f"Processes matching '{pattern}' ({len(matches)})",
        rows,
        header_line=header_line,
    )


def _kill_local_process():
    """Kill a process by PID."""
    pid_str = prompt_text("PID to kill:")
    if not pid_str:
        return
    try:
        pid = int(pid_str)
    except ValueError:
        error("Invalid PID.")
        return

    try:
        p = psutil.Process(pid)
        name = p.name()
    except psutil.NoSuchProcess:
        error(f"No process with PID {pid}.")
        return
    except psutil.AccessDenied:
        error(f"Access denied for PID {pid}.")
        return

    sig_idx = pick_option(f"Kill '{name}' (PID {pid}):", [
        "SIGTERM (graceful)",
        "SIGKILL (force)",
        "Cancel",
    ])
    if sig_idx == 2:
        return

    if not confirm(f"Kill '{name}' (PID {pid})?", default_yes=False):
        warn("Cancelled.")
        return

    try:
        if sig_idx == 0:
            p.terminate()
            success(f"Sent SIGTERM to {name} (PID {pid})")
        else:
            p.kill()
            success(f"Sent SIGKILL to {name} (PID {pid})")
        log_action("Local Kill Process", f"{name} (PID {pid})")
    except psutil.NoSuchProcess:
        warn("Process already exited.")
    except psutil.AccessDenied:
        error("Access denied. Try running with elevated privileges.")


# ─── Network Tools ────────────────────────────────────────────────────────────

def _network_tools():
    """Network tools submenu."""
    while True:
        idx = pick_option("Network Tools:", [
            "Interfaces        — local IPs and MAC addresses",
            "Active Connections — open ports and connections",
            "Public IP          — external IP address",
            "DNS Lookup         — resolve hostname to IP",
            "Port Check         — test if a port is open",
            "← Back",
        ])
        if idx == 5:
            return
        elif idx == 0:
            _network_interfaces()
        elif idx == 1:
            _active_connections()
        elif idx == 2:
            _public_ip()
        elif idx == 3:
            _dns_lookup()
        elif idx == 4:
            _port_check()


def _network_interfaces():
    """List network interfaces with IPs and MACs."""
    if not HAS_PSUTIL:
        warn("psutil is required.")
        return

    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    rows = []
    for iface in sorted(addrs.keys()):
        st = stats.get(iface)
        status = f"{C.GREEN}UP{C.RESET}" if (st and st.isup) else f"{C.RED}DOWN{C.RESET}"
        speed = f"  {st.speed}Mbps" if (st and st.speed) else ""
        rows.append(f"  {C.BOLD}{iface}{C.RESET}  {status}{speed}")

        for addr in addrs[iface]:
            family = addr.family.name if hasattr(addr.family, "name") else str(addr.family)
            if "AF_INET6" in family:
                rows.append(f"    IPv6: {addr.address}")
            elif "AF_INET" in family:
                mask = f"  /{addr.netmask}" if addr.netmask else ""
                rows.append(f"    IPv4: {C.ACCENT}{addr.address}{C.RESET}{mask}")
            elif "AF_LINK" in family or "AF_PACKET" in family:
                rows.append(f"    MAC:  {addr.address}")
        rows.append("")

    scrollable_list("Network Interfaces", rows)


def _active_connections():
    """List active network connections."""
    if not HAS_PSUTIL:
        warn("psutil is required.")
        return

    idx = pick_option("Connection filter:", [
        "Listening        — ports this machine is listening on",
        "Established      — active connections",
        "All              — all connections",
        "← Back",
    ])
    if idx == 3:
        return

    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        warn("Access denied. Some connections may require elevated privileges.")
        try:
            conns = psutil.net_connections(kind="inet4")
        except psutil.AccessDenied:
            error("Cannot read connections. Try running with elevated privileges.")
            return

    if idx == 0:
        conns = [c for c in conns if c.status == "LISTEN"]
    elif idx == 1:
        conns = [c for c in conns if c.status == "ESTABLISHED"]

    conns.sort(key=lambda c: (c.laddr.port if c.laddr else 0))

    rows = []
    for c in conns[:100]:
        local = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "?"
        remote = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
        pid = c.pid or "-"
        pname = ""
        if c.pid:
            try:
                pname = psutil.Process(c.pid).name()[:20]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        status_color = C.GREEN if c.status == "LISTEN" else (
            C.ACCENT if c.status == "ESTABLISHED" else C.DIM
        )
        rows.append(
            f"  {local:<25} {remote:<25} "
            f"{status_color}{c.status:<12}{C.RESET} "
            f"{pid:<7} {pname}"
        )

    header_line = (
        f"  {'LOCAL':25} {'REMOTE':25} {'STATUS':12} {'PID':7} PROCESS"
    )
    labels = ["Listening", "Established", "All"]
    scrollable_list(
        f"{labels[idx]} Connections ({len(rows)})",
        rows,
        header_line=header_line,
    )


def _public_ip():
    """Fetch and display public IP address."""
    info("Fetching public IP...")
    try:
        req = urllib.request.Request(
            "https://api.ipify.org",
            headers={"User-Agent": "homelab-cli"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            ip = resp.read().decode().strip()
        print(f"\n  {C.BOLD}Public IP:{C.RESET} {C.ACCENT}{ip}{C.RESET}\n")
        if confirm("Copy to clipboard?", default_yes=False):
            if copy_to_clipboard(ip):
                success("Copied.")
            else:
                error("Copy failed.")
    except Exception as e:
        error(f"Failed to fetch public IP: {e}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _dns_lookup():
    """Resolve a hostname to IP addresses."""
    hostname = prompt_text("Hostname to resolve:")
    if not hostname:
        return

    info(f"Resolving {hostname}...")
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        error(f"DNS lookup failed: {e}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    seen = set()
    print(f"\n  {C.BOLD}DNS results for {hostname}:{C.RESET}\n")
    for family, _, _, _, sockaddr in results:
        ip = sockaddr[0]
        if ip in seen:
            continue
        seen.add(ip)
        fam = "IPv4" if family == socket.AF_INET else "IPv6"
        print(f"  {fam}: {C.ACCENT}{ip}{C.RESET}")

    if not seen:
        warn("No results.")
    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _port_check():
    """Check if a remote port is open."""
    host = prompt_text("Host (IP or hostname):")
    if not host:
        return
    port_str = prompt_text("Port:")
    if not port_str:
        return
    try:
        port = int(port_str)
    except ValueError:
        error("Invalid port number.")
        return

    info(f"Checking {host}:{port}...")
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        success(f"{host}:{port} is {C.GREEN}OPEN{C.RESET}")
    except ConnectionRefusedError:
        warn(f"{host}:{port} is {C.RED}CLOSED{C.RESET} (connection refused)")
    except socket.timeout:
        warn(f"{host}:{port} is {C.YELLOW}FILTERED{C.RESET} (timeout)")
    except OSError as e:
        error(f"Connection failed: {e}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Disk Usage Analyzer ─────────────────────────────────────────────────────

def _dir_size_fast(path, max_depth=3, _depth=0):
    """Compute directory size, limiting recursion depth for speed."""
    total = 0
    if _depth > max_depth:
        return total
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += _dir_size_fast(entry.path, max_depth, _depth + 1)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def _disk_usage_analyzer():
    """Interactive local disk usage analyzer."""
    start = pick_option("Analyze:", [
        f"Home directory ({os.path.expanduser('~')})",
        "Root (/)",
        "Enter path manually",
        "← Back",
    ])
    if start == 3:
        return
    elif start == 0:
        path = os.path.expanduser("~")
    elif start == 1:
        path = "/"
    else:
        path = prompt_text("Path to analyze:")
        if not path:
            return
        path = os.path.expanduser(path)

    if not os.path.isdir(path):
        error("Not a valid directory.")
        return

    _analyze_local_dir(path)


def _analyze_local_dir(path):
    """Scan directory and show top space consumers with drill-down."""
    while True:
        info(f"Scanning {path}...")

        entries = []
        try:
            with os.scandir(path) as it:
                items = list(it)
        except (PermissionError, OSError) as e:
            error(f"Cannot read directory: {e}")
            return

        for i, entry in enumerate(items):
            if entry.name.startswith("."):
                continue
            print(f"\r  Scanning ({i + 1}/{len(items)})...", end="", flush=True)
            try:
                if entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                    entries.append((entry.name, size, False))
                elif entry.is_dir(follow_symlinks=False):
                    size = _dir_size_fast(entry.path)
                    entries.append((entry.name, size, True))
            except (PermissionError, OSError):
                continue

        print("\r" + " " * 40 + "\r", end="")

        if not entries:
            warn("Directory is empty.")
            return

        entries.sort(key=lambda x: x[1], reverse=True)
        top = entries[:25]

        if not top:
            return

        max_size = top[0][1] if top[0][1] > 0 else 1

        clear_screen()
        total_size = sum(s for _, s, _ in entries)
        print(f"\n  {C.BOLD}Disk Usage: {path}{C.RESET}")
        print(f"  {C.DIM}Total: {_format_size(total_size)} "
              f"({len(entries)} items){C.RESET}\n")

        choices = []
        if path != "/":
            choices.append(".. (go up)")

        for name, size, is_dir in top:
            pct = (size / total_size * 100) if total_size > 0 else 0
            bar_width = int((size / max_size) * 20) if max_size > 0 else 0
            bar = "█" * bar_width + "░" * (20 - bar_width)
            suffix = "/" if is_dir else ""
            display = name[:30] + suffix
            choices.append(
                f"  {display:<32} {bar} {_format_size(size):>8} {pct:>5.1f}%"
            )

        choices.append("← Back")

        idx = pick_option("", choices)
        choice = choices[idx]

        if choice == "← Back":
            return
        elif choice == ".. (go up)":
            path = os.path.dirname(path)
        else:
            has_up = 1 if path != "/" else 0
            item_idx = idx - has_up
            if item_idx < 0 or item_idx >= len(top):
                continue
            name, size, is_dir = top[item_idx]
            if is_dir:
                path = os.path.join(path, name)
            else:
                info(f"{name}: {_format_size(size)}")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ═════════════════════════════════════════════════════════════════════════════
#  LOCAL DOCKER (requires docker CLI)
# ═════════════════════════════════════════════════════════════════════════════

def _docker_run(args, capture=True):
    """Run a local docker command and return CompletedProcess."""
    cmd = ["docker"] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(cmd)


def _local_docker_menu():
    """Local Docker management menu."""
    if not _has_docker():
        warn("Docker CLI not found.")
        return

    while True:
        idx = pick_option("Local Docker:", [
            "Containers        — manage local containers",
            "Compose           — manage compose projects",
            "Stats             — live container resource usage",
            "Resource Graphs   — sparkline CPU/memory trends",
            "───────────────",
            "Images            — list, remove, prune images",
            "System Prune      — reclaim disk space",
            "───────────────",
            "← Back",
        ])
        if idx == 8:
            return
        elif idx in (4, 7):
            continue
        elif idx == 0:
            _local_docker_containers()
        elif idx == 1:
            _local_docker_compose()
        elif idx == 2:
            _local_docker_stats()
        elif idx == 3:
            _local_docker_resource_graph()
        elif idx == 5:
            _local_docker_images()
        elif idx == 6:
            _local_docker_prune()


def _local_docker_containers():
    """List and manage local Docker containers."""
    while True:
        result = _docker_run([
            "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"
        ])
        if result.returncode != 0 or not result.stdout.strip():
            warn("No Docker containers found (is Docker running?).")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        containers = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 3:
                containers.append({
                    "name": parts[0], "status": parts[1], "image": parts[2],
                })

        running = sum(1 for c in containers if "Up" in c["status"])
        stopped = len(containers) - running

        choices = []
        for c in containers:
            name = c["name"][:25]
            status = c["status"][:30]
            if "Up" in c["status"]:
                indicator = f"{C.GREEN}●{C.RESET}"
            else:
                indicator = f"{C.RED}●{C.RESET}"
            choices.append(f"  {indicator} {name:<25} {C.DIM}{status}{C.RESET}")

        choices.extend([
            "───────────────",
            "Bulk: Start all stopped",
            "Bulk: Stop all running",
            "Bulk: Restart all",
            "← Back",
        ])

        hdr = (
            f"\n  {C.BOLD}Local Docker Containers{C.RESET}\n"
            f"  {C.GREEN}●{C.RESET} {running} running  "
            f"{C.RED}●{C.RESET} {stopped} stopped\n"
        )
        idx = pick_option("", choices, header=hdr)

        n = len(containers)
        if idx == n + 4:  # Back
            return
        elif idx == n:  # separator
            continue
        elif idx == n + 1:  # Start all
            stopped_names = [c["name"] for c in containers if "Up" not in c["status"]]
            if not stopped_names:
                info("All containers are already running.")
                continue
            if confirm(f"Start {len(stopped_names)} stopped container(s)?"):
                for name in stopped_names:
                    r = _docker_run(["start", name])
                    if r.returncode == 0:
                        success(f"Started: {name}")
                    else:
                        error(f"Failed: {name}")
        elif idx == n + 2:  # Stop all
            running_names = [c["name"] for c in containers if "Up" in c["status"]]
            if not running_names:
                info("No running containers.")
                continue
            if confirm(f"Stop {len(running_names)} running container(s)?", default_yes=False):
                for name in running_names:
                    r = _docker_run(["stop", name])
                    if r.returncode == 0:
                        success(f"Stopped: {name}")
                    else:
                        error(f"Failed: {name}")
        elif idx == n + 3:  # Restart all
            if confirm(f"Restart all {len(containers)} container(s)?", default_yes=False):
                for c in containers:
                    r = _docker_run(["restart", c["name"]])
                    if r.returncode == 0:
                        success(f"Restarted: {c['name']}")
                    else:
                        error(f"Failed: {c['name']}")
        elif idx < n:
            _local_container_actions(containers[idx])


def _local_container_actions(container):
    """Actions for a single local Docker container."""
    cname = container["name"]
    is_running = "Up" in container["status"]

    while True:
        items = []
        if is_running:
            items.extend(["Stop", "Restart", "Shell", "Logs (follow)", "Logs (static)"])
        else:
            items.extend(["Start"])
        items.extend(["Inspect", "Update (pull & restart)", "Remove", "← Back"])

        idx = pick_option(f"{cname} ({container['status'][:30]}):", items)
        action = items[idx]

        if action == "← Back":
            return
        elif action == "Start":
            r = _docker_run(["start", cname])
            if r.returncode == 0:
                success(f"Started: {cname}")
                log_action("Docker Start", cname)
                is_running = True
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Stop":
            r = _docker_run(["stop", cname])
            if r.returncode == 0:
                success(f"Stopped: {cname}")
                log_action("Docker Stop", cname)
                is_running = False
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Restart":
            r = _docker_run(["restart", cname])
            if r.returncode == 0:
                success(f"Restarted: {cname}")
                log_action("Docker Restart", cname)
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Shell":
            # Try bash, fall back to sh
            subprocess.run(["docker", "exec", "-it", cname, "bash"])
        elif action == "Logs (follow)":
            subprocess.run(["docker", "logs", "-f", "--tail", "100", cname])
        elif action == "Logs (static)":
            r = _docker_run(["logs", "--tail", "100", cname])
            if r.returncode == 0:
                lines = r.stdout.strip().splitlines()
                scrollable_list(f"Logs: {cname}", [f"  {ln}" for ln in lines])
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Inspect":
            r = _docker_run(["inspect", cname])
            if r.returncode == 0:
                lines = r.stdout.strip().splitlines()[:80]
                scrollable_list(f"Inspect: {cname}", [f"  {ln}" for ln in lines])
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Update (pull & restart)":
            image = container.get("image", "")
            if not image:
                error("No image found for container.")
                continue
            if not confirm(f"Pull {image} and restart {cname}?"):
                continue
            info(f"Pulling {image}...")
            r = _docker_run(["pull", image])
            if r.returncode != 0:
                error(f"Pull failed: {r.stderr.strip()[:100]}")
                continue
            success(f"Pulled: {image}")
            info(f"Restarting {cname}...")
            r2 = _docker_run(["restart", cname])
            if r2.returncode == 0:
                success(f"Restarted: {cname}")
                log_action("Docker Update", f"{cname} ({image})")
            else:
                error(f"Restart failed: {r2.stderr.strip()[:100]}")
        elif action == "Remove":
            if not confirm(f"Remove container '{cname}'?", default_yes=False):
                continue
            if is_running:
                _docker_run(["stop", cname])
            r = _docker_run(["rm", cname])
            if r.returncode == 0:
                success(f"Removed: {cname}")
                log_action("Docker Remove", cname)
                return
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")


def _local_docker_compose():
    """Manage local Docker Compose projects."""
    result = _docker_run(["compose", "ls", "--format", "json"])
    if result.returncode != 0 or not result.stdout.strip():
        warn("No compose projects found (requires Docker Compose v2).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    try:
        projects = json.loads(result.stdout)
    except json.JSONDecodeError:
        error("Could not parse compose output.")
        return

    if not projects:
        info("No compose projects found.")
        return

    choices = []
    for p in projects:
        name = p.get("Name", "?")
        status = p.get("Status", "?")[:40]
        choices.append(f"  {name:<25} {C.DIM}{status}{C.RESET}")
    choices.append("← Back")

    idx = pick_option("Compose Projects:", choices)
    if idx >= len(projects):
        return

    proj = projects[idx]
    proj_name = proj.get("Name", "?")
    config_files = proj.get("ConfigFiles", "")

    while True:
        action_idx = pick_option(f"Compose: {proj_name}", [
            "Up (start)",
            "Down (stop & remove)",
            "Pull & Up (update)",
            "Restart",
            "Logs",
            "← Back",
        ])
        if action_idx == 5:
            return

        base_cmd = ["compose"]
        if config_files:
            for cf in config_files.split(","):
                base_cmd.extend(["-f", cf.strip()])
        else:
            base_cmd.extend(["-p", proj_name])

        if action_idx == 0:  # Up
            info(f"Starting {proj_name}...")
            r = _docker_run(base_cmd + ["up", "-d"])
            if r.returncode == 0:
                success(f"Started: {proj_name}")
                log_action("Docker Compose Up", proj_name)
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action_idx == 1:  # Down
            if confirm(f"Stop and remove {proj_name}?", default_yes=False):
                r = _docker_run(base_cmd + ["down"])
                if r.returncode == 0:
                    success(f"Stopped: {proj_name}")
                    log_action("Docker Compose Down", proj_name)
                else:
                    error(f"Failed: {r.stderr.strip()[:100]}")
        elif action_idx == 2:  # Pull & Up
            info(f"Pulling images for {proj_name}...")
            r = _docker_run(base_cmd + ["pull"])
            if r.returncode != 0:
                error(f"Pull failed: {r.stderr.strip()[:100]}")
                continue
            success("Pull complete.")
            info("Starting updated containers...")
            r2 = _docker_run(base_cmd + ["up", "-d"])
            if r2.returncode == 0:
                success(f"Updated: {proj_name}")
                log_action("Docker Compose Update", proj_name)
            else:
                error(f"Start failed: {r2.stderr.strip()[:100]}")
        elif action_idx == 3:  # Restart
            r = _docker_run(base_cmd + ["restart"])
            if r.returncode == 0:
                success(f"Restarted: {proj_name}")
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action_idx == 4:  # Logs
            subprocess.run(["docker"] + base_cmd + ["logs", "--tail", "100", "-f"])


def _local_docker_stats():
    """Show live Docker stats."""
    result = _docker_run([
        "stats", "--no-stream", "--format",
        "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}"
    ])
    if result.returncode != 0 or not result.stdout.strip():
        warn("No running containers.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    rows = []
    for line in result.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 6:
            name = parts[0][:25]
            cpu = parts[1]
            mem_usage = parts[2]
            mem_pct = parts[3]
            net_io = parts[4]
            block_io = parts[5]
            rows.append(
                f"  {name:<25} {cpu:>8} {mem_pct:>8} "
                f"{mem_usage:>20}  {net_io:>20}  {block_io:>20}"
            )

    header_line = (
        f"  {'NAME':<25} {'CPU':>8} {'MEM%':>8} "
        f"{'MEM USAGE':>20}  {'NET I/O':>20}  {'BLOCK I/O':>20}"
    )
    scrollable_list(f"Docker Stats ({len(rows)} containers)", rows, header_line=header_line)


def _local_docker_resource_graph():
    """Docker resource sparkline trends from multiple samples."""
    samples = 5
    interval = 2
    history = {}

    print()
    for i in range(samples):
        if i > 0:
            time.sleep(interval)
        print(f"\r  Collecting sample {i + 1}/{samples}...", end="", flush=True)

        result = _docker_run([
            "stats", "--no-stream", "--format",
            "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.MemUsage}}"
        ])
        if result.returncode != 0 or not result.stdout.strip():
            continue

        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 4:
                name = parts[0]
                try:
                    cpu_val = float(parts[1].rstrip("%"))
                except ValueError:
                    cpu_val = 0.0
                try:
                    mem_val = float(parts[2].rstrip("%"))
                except ValueError:
                    mem_val = 0.0
                mem_usage = parts[3]
                if name not in history:
                    history[name] = {"cpu": [], "mem": [], "mem_usage": ""}
                history[name]["cpu"].append(cpu_val)
                history[name]["mem"].append(mem_val)
                history[name]["mem_usage"] = mem_usage

    print("\r" + " " * 40 + "\r", end="")

    if not history:
        warn("No running containers.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    clear_screen()
    print(f"\n  {C.BOLD}Docker Resource Graphs{C.RESET}  "
          f"{C.DIM}({samples} samples, {interval}s interval){C.RESET}\n")

    max_name = min(28, max(len(n) for n in history.keys()))

    print(f"  {'NAME':<{max_name}}  {'CPU':>{samples + 7}}  {'MEMORY':>{samples + 22}}")
    print(f"  {'─' * max_name}  {'─' * (samples + 7)}  {'─' * (samples + 22)}")

    for name in sorted(history.keys()):
        data = history[name]
        cpu_vals = data["cpu"]
        mem_vals = data["mem"]
        last_cpu = cpu_vals[-1] if cpu_vals else 0
        mem_usage = data["mem_usage"]

        cpu_spark = sparkline(cpu_vals, width=samples)
        mem_spark = sparkline(mem_vals, width=samples)

        display_name = name if len(name) <= max_name else name[:max_name - 1] + "…"

        print(f"  {display_name:<{max_name}}  {cpu_spark} {last_cpu:>5.1f}%  "
              f"{mem_spark} {mem_usage:>20}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _local_docker_images():
    """List and manage local Docker images."""
    while True:
        result = _docker_run([
            "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}"
        ])
        if result.returncode != 0 or not result.stdout.strip():
            warn("No images found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        images = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 4:
                images.append({
                    "repo_tag": parts[0], "id": parts[1],
                    "size": parts[2], "created": parts[3],
                })

        choices = []
        for img in images:
            repo = img["repo_tag"][:40]
            choices.append(f"  {repo:<40} {img['size']:>10}  {C.DIM}{img['created']}{C.RESET}")

        choices.extend([
            "───────────────",
            "Prune dangling images",
            "Prune ALL unused images",
            "← Back",
        ])

        hdr = f"\n  {C.BOLD}Docker Images ({len(images)}){C.RESET}\n"
        idx = pick_option("", choices, header=hdr)

        n = len(images)
        if idx == n + 3:  # Back
            return
        elif idx == n:  # separator
            continue
        elif idx == n + 1:  # Prune dangling
            if confirm("Remove all dangling (untagged) images?"):
                r = _docker_run(["image", "prune", "-f"])
                if r.returncode == 0:
                    success("Pruned dangling images.")
                    log_action("Docker Prune", "dangling images")
                else:
                    error(f"Failed: {r.stderr.strip()[:100]}")
        elif idx == n + 2:  # Prune all
            if confirm("Remove ALL unused images? This may free significant space.", default_yes=False):
                r = _docker_run(["image", "prune", "-af"])
                if r.returncode == 0:
                    success("Pruned all unused images.")
                    log_action("Docker Prune", "all unused images")
                else:
                    error(f"Failed: {r.stderr.strip()[:100]}")
        elif idx < n:
            img = images[idx]
            action = pick_option(f"{img['repo_tag']}:", [
                "Remove image", "Inspect", "Cancel",
            ])
            if action == 0:
                if confirm(f"Remove {img['repo_tag']}?", default_yes=False):
                    r = _docker_run(["rmi", img["id"]])
                    if r.returncode == 0:
                        success(f"Removed: {img['repo_tag']}")
                        log_action("Docker Remove Image", img["repo_tag"])
                    else:
                        error(f"Failed: {r.stderr.strip()[:100]}")
            elif action == 1:
                r = _docker_run(["image", "inspect", img["id"]])
                if r.returncode == 0:
                    lines = r.stdout.strip().splitlines()[:60]
                    scrollable_list(f"Image: {img['repo_tag']}", [f"  {ln}" for ln in lines])


def _local_docker_prune():
    """Docker system prune."""
    # Show current disk usage
    result = _docker_run(["system", "df"])
    if result.returncode == 0:
        print(f"\n  {C.BOLD}Docker Disk Usage:{C.RESET}\n")
        for line in result.stdout.strip().splitlines():
            print(f"  {line}")
        print()

    idx = pick_option("System Prune:", [
        "Basic prune   — stopped containers, unused networks, dangling images",
        "Full prune    — above + all unused images and build cache",
        "← Back",
    ])
    if idx == 2:
        return
    elif idx == 0:
        if confirm("Run basic system prune?"):
            r = _docker_run(["system", "prune", "-f"])
            if r.returncode == 0:
                success("System prune complete.")
                for line in r.stdout.strip().splitlines()[-3:]:
                    print(f"  {line}")
                log_action("Docker System Prune", "basic")
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
    elif idx == 1:
        if confirm("Run FULL system prune? This removes all unused data.", default_yes=False):
            r = _docker_run(["system", "prune", "-af", "--volumes"])
            if r.returncode == 0:
                success("Full system prune complete.")
                for line in r.stdout.strip().splitlines()[-3:]:
                    print(f"  {line}")
                log_action("Docker System Prune", "full")
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ═════════════════════════════════════════════════════════════════════════════
#  SERVICE MANAGER (systemctl / launchctl)
# ═════════════════════════════════════════════════════════════════════════════

def _service_manager_menu():
    """Platform-adaptive service manager."""
    sm = _service_manager()
    if not sm:
        warn("No supported service manager found.")
        return

    while True:
        if sm == "systemctl":
            idx = pick_option("Systemd Services:", [
                "Active services   — currently running",
                "Failed services   — units in error state",
                "All services      — every loaded unit",
                "← Back",
            ])
            if idx == 3:
                return
            filters = ["--state=active", "--state=failed", ""]
            _list_systemd_services(filters[idx])
        else:
            idx = pick_option("launchctl Services:", [
                "Running services  — currently loaded and running",
                "All loaded        — all loaded services",
                "← Back",
            ])
            if idx == 2:
                return
            _list_launchctl_services(running_only=(idx == 0))


def _list_systemd_services(state_filter):
    """List systemd services with optional state filter."""
    cmd = ["systemctl", "list-units", "--type=service", "--no-pager", "--plain"]
    if state_filter:
        cmd.append(state_filter)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        warn("No services found.")
        return

    lines = result.stdout.strip().splitlines()
    services = []
    rows = []

    for line in lines:
        # Skip header and summary lines
        if line.startswith("UNIT") or line.startswith(" ") or "loaded units" in line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit = parts[0]
        if not unit.endswith(".service"):
            continue
        active = parts[2]
        sub = parts[3]
        desc = parts[4] if len(parts) > 4 else ""

        name = unit.replace(".service", "")
        services.append(name)

        if active == "active":
            status = f"{C.GREEN}{sub}{C.RESET}"
        elif active == "failed":
            status = f"{C.RED}failed{C.RESET}"
        else:
            status = f"{C.YELLOW}{active}/{sub}{C.RESET}"

        rows.append(f"  {name:<35} {status:<20} {C.DIM}{desc[:40]}{C.RESET}")

    if not rows:
        warn("No services found.")
        return

    header_line = f"  {'SERVICE':<35} {'STATUS':<20} {'DESCRIPTION'}"
    result_idx = scrollable_list(
        f"Services ({len(rows)})", rows, header_line=header_line,
    )
    if result_idx is not None and result_idx < len(services):
        _systemd_service_actions(services[result_idx])


def _systemd_service_actions(service_name):
    """Actions for a single systemd service."""
    while True:
        # Get current status
        result = subprocess.run(
            ["systemctl", "is-active", f"{service_name}.service"],
            capture_output=True, text=True,
        )
        is_active = result.stdout.strip() == "active"

        items = []
        if is_active:
            items.extend(["Stop", "Restart"])
        else:
            items.extend(["Start"])
        items.extend(["Status (full)", "Logs (last 50)", "← Back"])

        idx = pick_option(f"Service: {service_name}", items)
        action = items[idx]

        if action == "← Back":
            return
        elif action == "Start":
            r = subprocess.run(
                ["sudo", "systemctl", "start", f"{service_name}.service"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                success(f"Started: {service_name}")
                log_action("Service Start", service_name)
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Stop":
            if confirm(f"Stop {service_name}?", default_yes=False):
                r = subprocess.run(
                    ["sudo", "systemctl", "stop", f"{service_name}.service"],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    success(f"Stopped: {service_name}")
                    log_action("Service Stop", service_name)
                else:
                    error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Restart":
            r = subprocess.run(
                ["sudo", "systemctl", "restart", f"{service_name}.service"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                success(f"Restarted: {service_name}")
                log_action("Service Restart", service_name)
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Status (full)":
            r = subprocess.run(
                ["systemctl", "status", f"{service_name}.service", "--no-pager"],
                capture_output=True, text=True,
            )
            lines = r.stdout.strip().splitlines() if r.stdout else []
            if r.stderr:
                lines.extend(r.stderr.strip().splitlines())
            scrollable_list(f"Status: {service_name}", [f"  {ln}" for ln in lines])
        elif action == "Logs (last 50)":
            r = subprocess.run(
                ["journalctl", "-u", f"{service_name}.service", "-n", "50", "--no-pager"],
                capture_output=True, text=True,
            )
            lines = r.stdout.strip().splitlines() if r.stdout else ["No logs found."]
            scrollable_list(f"Logs: {service_name}", [f"  {ln}" for ln in lines])


def _list_launchctl_services(running_only=False):
    """List launchctl services."""
    result = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        warn("Could not list services.")
        return

    lines = result.stdout.strip().splitlines()
    services = []
    rows = []

    for line in lines[1:]:  # Skip header
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid = parts[0].strip()
        exit_code = parts[1].strip()
        label = parts[2].strip()

        # Skip Apple internal services for readability
        if label.startswith("com.apple.") and not label.startswith("com.apple.finder"):
            continue

        is_running = pid != "-" and pid != ""

        if running_only and not is_running:
            continue

        services.append(label)

        if is_running:
            status = f"{C.GREEN}running (PID {pid}){C.RESET}"
        elif exit_code != "0":
            status = f"{C.RED}exited ({exit_code}){C.RESET}"
        else:
            status = f"{C.DIM}stopped{C.RESET}"

        rows.append(f"  {label:<45} {status}")

    if not rows:
        info("No services found.")
        return

    header_line = f"  {'LABEL':<45} STATUS"
    result_idx = scrollable_list(
        f"Services ({len(rows)})", rows, header_line=header_line,
    )
    if result_idx is not None and result_idx < len(services):
        _launchctl_service_actions(services[result_idx])


def _launchctl_service_actions(label):
    """Actions for a single launchctl service."""
    while True:
        # Check if running
        result = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True,
        )
        is_running = result.returncode == 0 and "PID" in result.stdout

        items = []
        if is_running:
            items.extend(["Stop (kill)", "Restart"])
        else:
            items.extend(["Start (kickstart)"])
        items.extend(["Info", "← Back"])

        idx = pick_option(f"Service: {label}", items)
        action = items[idx]

        if action == "← Back":
            return
        elif action == "Start (kickstart)":
            r = subprocess.run(
                ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                success(f"Started: {label}")
                log_action("Service Start", label)
            else:
                error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Stop (kill)":
            if confirm(f"Stop {label}?", default_yes=False):
                r = subprocess.run(
                    ["launchctl", "kill", "SIGTERM", f"gui/{os.getuid()}/{label}"],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    success(f"Stopped: {label}")
                    log_action("Service Stop", label)
                else:
                    error(f"Failed: {r.stderr.strip()[:100]}")
        elif action == "Restart":
            subprocess.run(
                ["launchctl", "kill", "SIGTERM", f"gui/{os.getuid()}/{label}"],
                capture_output=True, text=True,
            )
            time.sleep(1)
            r = subprocess.run(
                ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                success(f"Restarted: {label}")
                log_action("Service Restart", label)
            else:
                error(f"Restart failed: {r.stderr.strip()[:100]}")
        elif action == "Info":
            r = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True,
            )
            lines = r.stdout.strip().splitlines() if r.stdout else ["No info available."]
            scrollable_list(f"Info: {label}", [f"  {ln}" for ln in lines])
