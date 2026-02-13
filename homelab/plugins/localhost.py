"""Local Files plugin — browse, search, and manage files on localhost."""

import datetime
import hashlib
import os
import platform
import re
import shutil
import subprocess

from homelab.config import CFG
from homelab.modules.auditlog import log_action
from homelab.modules.transport import format_item
from homelab.notifications import copy_to_clipboard
from homelab.plugins import Plugin, add_plugin_favorite
from homelab.ui import (
    C, pick_option, pick_multi, confirm, prompt_text,
    info, success, error, warn, clear_screen,
)

# ─── Text file extensions for preview/edit detection ──────────────────────────

TEXT_EXT = (
    ".txt", ".md", ".json", ".xml", ".csv", ".log", ".yml", ".yaml",
    ".conf", ".cfg", ".ini", ".sh", ".py", ".js", ".html", ".css",
    ".nfo", ".srt", ".sub", ".env", ".toml", ".properties",
)

ARCHIVE_EXT = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".rar", ".7z")


# ─── Plugin Class ─────────────────────────────────────────────────────────────

class LocalhostPlugin(Plugin):
    name = "Localhost"
    key = "localhost"

    def is_configured(self):
        return True

    def get_config_fields(self):
        return []

    def get_menu_items(self):
        return [
            ("Localhost             — localhost toolset", localhost_menu),
        ]

    def get_actions(self):
        return {
            "Local File Manager": ("localhost_file_manager", _manage_local),
            "Local Trash": ("localhost_trash", _manage_local_trash),
        }


# ─── Top-level Menu ───────────────────────────────────────────────────────────

def localhost_menu():
    while True:
        idx = pick_option("Local Files:", [
            "File Manager         — browse, search, rename, move, delete",
            "Trash                — view and restore deleted files",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 2:
            continue
        elif idx == 3:
            add_plugin_favorite(LocalhostPlugin())
        elif idx == 0:
            _manage_local()
        elif idx == 1:
            _manage_local_trash()


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
    if platform.system() == "Darwin":
        return "open"
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
