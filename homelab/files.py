"""File manager — browse, upload, download, search, and file operations."""

import datetime
import os
import re
import shutil
import subprocess
import tempfile
import time

from homelab.config import CFG, save_config, local_hostname
from homelab.ui import (
    C, pick_option, pick_multi, confirm, prompt_text, info, success, error,
    warn, check_tool, clear_screen,
)
from homelab.transport import (
    ssh_run, rsync_transfer, _check_disk_space,
    list_remote_dirs, list_remote_items, format_item,
    pick_rsync_options,
)
from homelab.auditlog import log_action
from homelab.history import log_transfer
from homelab.notifications import notify, copy_to_clipboard

# ─── Text file extensions for preview/edit detection ────────────────────────

TEXT_EXT = (
    ".txt", ".md", ".json", ".xml", ".csv", ".log", ".yml", ".yaml",
    ".conf", ".cfg", ".ini", ".sh", ".py", ".js", ".html", ".css",
    ".nfo", ".srt", ".sub", ".env", ".toml", ".properties",
)

# ─── Fetch methods ──────────────────────────────────────────────────────────

FETCH_METHODS = [
    ("wget", "wget"),
    ("git clone", "git"),
    ("curl", "curl"),
    ("yt-dlp", "yt-dlp"),
]


# ─── Browse Remote Folder (picker) ─────────────────────────────────────────

def _is_at_root(current, base_path, extra_paths=None):
    """Check if current path is a navigation root (base or extra path)."""
    if current == base_path:
        return True
    for ep in (extra_paths or []):
        if current == ep:
            return True
    return False


def browse_remote_folder(host, base_path, extra_paths=None, port=None):
    current = base_path
    info(f"Browsing folders on {host}:{base_path}")

    while True:
        dirs = list_remote_dirs(current, host=host, port=port)
        dir_names = [os.path.basename(d) for d in dirs]

        choices = ["** Use this folder **"]
        if not _is_at_root(current, base_path, extra_paths):
            choices.append(".. (go up)")
        if CFG["bookmarks"]:
            choices.append("★ Bookmarks")
        choices.append("★ Save bookmark")
        extra_labels = []
        if current == base_path:
            for ep in (extra_paths or []):
                label = f"[EXT]  {os.path.basename(ep)}/"
                extra_labels.append((label, ep))
                choices.append(label)
        for dn in dir_names:
            choices.append(f"{dn}/")
        choices.append("+ Create new folder")

        hdr = f"\n  {C.BOLD}Select destination:{C.RESET} {C.ACCENT}{current}/{C.RESET}\n"
        idx = pick_option("Select a folder:", choices, header=hdr)
        choice = choices[idx]

        if choice == "** Use this folder **":
            return current
        elif choice == ".. (go up)":
            current = os.path.dirname(current)
        elif choice == "★ Bookmarks":
            bm_choices = CFG["bookmarks"] + ["Cancel"]
            bm_idx = pick_option("Bookmarked folders:", bm_choices)
            if bm_idx < len(CFG["bookmarks"]):
                current = CFG["bookmarks"][bm_idx]
        elif choice == "★ Save bookmark":
            if current not in CFG["bookmarks"]:
                CFG["bookmarks"].append(current)
                save_config(CFG)
                success(f"Bookmarked: {current}")
            else:
                warn("Already bookmarked.")
        elif choice == "+ Create new folder":
            name = prompt_text("New folder name:")
            if not name:
                continue
            new_path = os.path.join(current, name)
            result = ssh_run(f"mkdir -p '{new_path}'", host=host, port=port)
            if result.returncode != 0:
                error(f"Failed to create folder: {result.stderr.strip()}")
            else:
                log_action("Folder Create", f"{host}:{new_path}")
                success(f"Created: {new_path}")
                current = new_path
        else:
            matched = False
            for label, ep in extra_labels:
                if choice == label:
                    current = ep
                    matched = True
                    break
            if not matched:
                current = os.path.join(current, choice.rstrip("/"))


# ─── Browse Local Files ─────────────────────────────────────────────────────

def browse_local():
    current = os.path.expanduser("~")

    while True:
        try:
            entries = sorted(os.listdir(current))
        except PermissionError:
            error("Permission denied.")
            current = os.path.dirname(current)
            continue

        dirs = [e for e in entries if os.path.isdir(os.path.join(current, e)) and not e.startswith(".")]
        files = [e for e in entries if os.path.isfile(os.path.join(current, e)) and not e.startswith(".")]

        choices = ["** Upload this folder **"]
        if current != "/":
            choices.append(".. (go up)")
        for d in dirs:
            choices.append(f"[DIR]  {d}/")
        for f_name in files:
            sz = os.path.getsize(os.path.join(current, f_name))
            if sz > 1_000_000:
                sz_str = f"{sz / 1_000_000:.1f} MB"
            elif sz > 1_000:
                sz_str = f"{sz / 1_000:.1f} KB"
            else:
                sz_str = f"{sz} B"
            choices.append(f"       {f_name}  ({sz_str})")
        choices.append("Enter path manually")
        choices.append("← Cancel")

        hdr = f"\n  {C.BOLD}Browse local:{C.RESET} {C.ACCENT}{current}/{C.RESET}\n"
        idx = pick_option("Select file or folder:", choices, header=hdr)
        choice = choices[idx]

        if choice == "← Cancel":
            return None
        elif choice == "** Upload this folder **":
            return current
        elif choice == "Enter path manually":
            path = prompt_text("Path:")
            path = os.path.expanduser(path)
            if os.path.exists(path):
                return path
            error("Path does not exist.")
        elif choice == ".. (go up)":
            current = os.path.dirname(current)
        else:
            offset = 2 if current != "/" else 1  # upload-this-folder + go-up
            item_idx = idx - offset
            if item_idx < len(dirs):
                selected_dir = os.path.join(current, dirs[item_idx])
                action = pick_option(
                    f"Selected folder: {dirs[item_idx]}/",
                    ["Open folder", "Upload this folder", "Cancel"],
                )
                if action == 0:
                    current = selected_dir
                elif action == 1:
                    return selected_dir
            else:
                file_idx = item_idx - len(dirs)
                if file_idx < len(files):
                    selected = os.path.join(current, files[file_idx])
                    action = pick_option(
                        f"Selected: {selected}",
                        ["Upload this file", "Cancel"],
                    )
                    if action == 0:
                        return selected


# ─── Fetch helpers ──────────────────────────────────────────────────────────

def choose_fetch_method():
    labels = []
    available = []
    for display_name, tool in FETCH_METHODS:
        if tool is None or check_tool(tool):
            labels.append(display_name)
        else:
            labels.append(f"{display_name} (not installed)")
        available.append((display_name, tool))

    idx = pick_option("Choose a download method:", labels)
    name, tool = available[idx]

    if tool and not check_tool(tool):
        error(f"'{tool}' is not installed on this system.")
        return None, None

    return name, tool


def fetch_files(method_name, tool, url, tmpdir):
    if CFG.get("dry_run"):
        info(f"[DRY RUN] Would download with {method_name}: {url}")
        return True

    info(f"Downloading with {method_name}...")

    if tool == "wget":
        cmd = ["wget", "-P", tmpdir, url]
    elif tool == "git":
        dest = os.path.join(tmpdir, os.path.basename(url).replace(".git", ""))
        cmd = ["git", "clone", url, dest]
    elif tool == "curl":
        filename = os.path.basename(url.split("?")[0]) or "download"
        cmd = ["curl", "-L", "-o", os.path.join(tmpdir, filename), url]
    elif tool == "yt-dlp":
        cmd = ["yt-dlp", "-o", os.path.join(tmpdir, "%(title)s.%(ext)s"), url]
    else:
        error(f"Unknown fetch method: {method_name}")
        return False

    result = subprocess.run(cmd)
    if result.returncode != 0:
        error(f"Download failed (exit code {result.returncode})")
        return False

    success("Download complete.")
    return True


def transfer_to_server(tmpdir, remote_path, host, port=None):
    items = os.listdir(tmpdir)
    if not items:
        error("No files were downloaded. Nothing to transfer.")
        return False

    if not _check_disk_space(remote_path, host=host):
        warn("Aborted.")
        return False

    info(f"Transferring {len(items)} item(s) to {host}:{remote_path}/")
    start = time.time()
    for item in items:
        local_path = os.path.join(tmpdir, item)
        is_dir = os.path.isdir(local_path)
        dest = f"{host}:{remote_path}/"
        print(f"    {C.ACCENT}→{C.RESET} {item}")
        result = rsync_transfer(local_path, dest, is_dir=is_dir, port=port)
        if result.returncode != 0:
            error(f"Failed to transfer {item}")
            notify("Homelab", f"Transfer failed: {item}")
            return False

    elapsed = time.time() - start
    success(f"Transfer complete! ({elapsed:.1f}s)")
    notify("Homelab", f"Transfer complete: {len(items)} item(s) in {elapsed:.1f}s")
    return True


# ─── 1. Fetch & Transfer (Transfer to Server) ──────────────────────────────

def fetch_and_transfer(host, base_path, extra_paths=None):
    method_name, tool = choose_fetch_method()
    if method_name is None:
        return

    if tool is None:
        url = None
    else:
        url = prompt_text(f"Enter the URL to {method_name}:")
        if not url:
            error("No URL provided.")
            return

    tmpdir = tempfile.mkdtemp(prefix="homelab_")
    try:
        if not fetch_files(method_name, tool, url, tmpdir):
            return

        remote_path = browse_remote_folder(host, base_path, extra_paths)

        items = os.listdir(tmpdir)
        if not items:
            error("No files were downloaded.")
            return

        if len(items) == 1:
            old = items[0]
            new_name = prompt_text(f"Rename (blank to keep '{old}'):")
            if new_name and new_name != old:
                os.rename(
                    os.path.join(tmpdir, old),
                    os.path.join(tmpdir, new_name),
                )
                items = [new_name]

        print(f"\n  {C.BOLD}Ready to transfer {len(items)} item(s):{C.RESET}")
        for item in items:
            sz_str = ""
            path = os.path.join(tmpdir, item)
            if os.path.isfile(path):
                sz = os.path.getsize(path)
                if sz > 1_000_000:
                    sz_str = f" {C.DIM}({sz / 1_000_000:.1f} MB){C.RESET}"
                elif sz > 1_000:
                    sz_str = f" {C.DIM}({sz / 1_000:.1f} KB){C.RESET}"
            print(f"    {C.ACCENT}•{C.RESET} {item}{sz_str}")
        print(
            f"  {C.BOLD}Destination:{C.RESET} "
            f"{C.ACCENT}{host}:{remote_path}/{C.RESET}"
        )

        if not confirm("Proceed?"):
            warn("Aborted.")
            return

        if transfer_to_server(tmpdir, remote_path, host):
            log_action("File Upload", f"{len(items)} item(s) via {method_name} → {host}:{remote_path}")
            log_transfer("fetch_transfer", method_name, url, remote_path, len(items))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 2. Upload Local Files ─────────────────────────────────────────────────

def upload_local(host, base_path, extra_paths=None, port=None):
    info("Select a file or folder to upload.")

    local_path = browse_local()
    if not local_path:
        return

    remote_path = browse_remote_folder(host, base_path, extra_paths)

    is_dir = os.path.isdir(local_path)
    name = os.path.basename(local_path)
    new_name = None

    # For directories, choose transfer mode (+ optional rename)
    sync_contents = False
    if is_dir:
        mode_opts = [
            f"Contents — merge files inside {name}/ into destination",
            f"Folder   — transfer {name}/ itself as a subfolder",
            "Rename   — transfer with a different folder name",
            "Back",
        ]
        mode_idx = pick_option("Transfer mode:", mode_opts)
        if mode_idx == 3:
            return
        if mode_idx == 2:
            new_name = prompt_text(f"New name (blank to keep '{name}'):")
            if not new_name:
                new_name = None
            # Renamed folder always syncs contents into new name
            sync_contents = True
        else:
            sync_contents = (mode_idx == 0)

    # Build source/dest paths
    if is_dir and sync_contents:
        source = local_path + "/"
        dest = f"{host}:{remote_path}/{new_name}" if new_name else f"{host}:{remote_path}/"
        if new_name:
            mode_hint = f"Contents of {name}/ synced into {remote_path}/{new_name}/"
        else:
            mode_hint = f"Contents of {name}/ merged into destination"
    elif is_dir:
        source = local_path
        dest = f"{host}:{remote_path}/"
        mode_hint = f"Folder {name}/ transferred as subfolder"
    else:
        source = local_path
        dest = f"{host}:{remote_path}/"
        mode_hint = f"File copied to {remote_path}/{name}"

    # Rsync options before summary so they appear in confirmation
    extra_args = pick_rsync_options(source=source, dest=dest, port=port)

    # Disk space check before summary
    if not _check_disk_space(remote_path, host=host):
        warn("Aborted.")
        return

    # Calculate total transfer size
    print(f"  {C.DIM}Calculating total transfer size...{C.RESET}", end="\r", flush=True)
    total_bytes = 0
    file_count = 0
    if is_dir:
        for root, _dirs, fnames in os.walk(local_path):
            for fn in fnames:
                fp = os.path.join(root, fn)
                if not os.path.islink(fp):
                    try:
                        total_bytes += os.path.getsize(fp)
                        file_count += 1
                    except OSError:
                        pass
    else:
        try:
            total_bytes = os.path.getsize(local_path)
        except OSError:
            pass
        file_count = 1

    if total_bytes >= 1_000_000_000:
        size_str = f"{total_bytes / (1024 ** 3):.1f} GB"
    elif total_bytes >= 1_000_000:
        size_str = f"{total_bytes / (1024 ** 2):.1f} MB"
    elif total_bytes >= 1_000:
        size_str = f"{total_bytes / 1024:.1f} KB"
    else:
        size_str = f"{total_bytes} B"
    if is_dir:
        size_str += f" ({file_count:,} files)"

    # Final confirmation summary
    dest_display = dest.split(":", 1)[1] if ":" in dest else dest
    display_name = new_name or name
    clear_screen()
    print(f"\n  {C.BOLD}Upload:{C.RESET} {display_name}")
    print(f"  {C.BOLD}From:{C.RESET}   {C.ACCENT}{local_path}{C.RESET}")
    print(f"  {C.BOLD}To:{C.RESET}     {C.ACCENT}{host}:{dest_display}{C.RESET}")
    print(f"  {C.BOLD}Mode:{C.RESET}   {C.DIM}{mode_hint}{C.RESET}")
    print(f"  {C.BOLD}Size:{C.RESET}   {size_str}")
    if extra_args:
        print(f"  {C.BOLD}Opts:{C.RESET}   {C.DIM}{' '.join(extra_args)}{C.RESET}")

    if not confirm("Proceed?"):
        warn("Aborted.")
        return

    start = time.time()
    result = rsync_transfer(source, dest, is_dir=is_dir, port=port, extra_args=extra_args)
    elapsed = time.time() - start
    if result.returncode != 0:
        error("Upload failed.")
        notify("Homelab", f"Upload failed: {name}")
    else:
        success(f"Uploaded: {name} ({elapsed:.1f}s)")
        notify("Homelab", f"Upload complete: {name}")
        log_action("File Upload", f"{name} → {host}:{remote_path}")
        log_transfer("upload", "rsync", local_path, remote_path, 1)


# ─── 3. Queue Download ─────────────────────────────────────────────────────

def queue_download(host, base_path, extra_paths=None):
    method_name, tool = choose_fetch_method()
    if method_name is None:
        return

    print(f"\n  {C.BOLD}Paste URLs one per line. Empty line to finish:{C.RESET}")
    urls = []
    while True:
        line = prompt_text("url>")
        if not line:
            break
        urls.append(line)

    if not urls:
        warn("No URLs entered.")
        return

    info(f"Queued {len(urls)} URL(s) with {method_name}")

    tmpdir = tempfile.mkdtemp(prefix="homelab_queue_")
    try:
        failed = []
        for i, url in enumerate(urls, 1):
            print(f"\n  {C.BOLD}[{i}/{len(urls)}]{C.RESET} {url}")
            if not fetch_files(method_name, tool, url, tmpdir):
                failed.append(url)

        items = os.listdir(tmpdir)
        if not items:
            error("No files downloaded.")
            return

        if failed:
            warn(f"{len(failed)} URL(s) failed:")
            for u in failed:
                print(f"    {C.RED}✗{C.RESET} {u}")

        success(f"{len(items)} item(s) downloaded successfully.")

        remote_path = browse_remote_folder(host, base_path, extra_paths)

        if not confirm(f"Transfer {len(items)} item(s) to {remote_path}?"):
            warn("Aborted.")
            return

        if transfer_to_server(tmpdir, remote_path, host):
            log_action("File Upload", f"{len(items)} item(s) via {method_name} (queued) → {host}:{remote_path}")
            log_transfer("queue_transfer", method_name, f"{len(urls)} URLs", remote_path, len(items))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 4. Manage Files ───────────────────────────────────────────────────────

def manage_files(host, base_path, extra_paths=None, trash_path=None, port=None):
    current = base_path

    while True:
        dirs, files = list_remote_items(current, host=host, port=port)
        all_items = dirs + files

        choices = []
        if not _is_at_root(current, base_path, extra_paths):
            choices.append(".. (go up)")

        extra_labels = []
        if current == base_path:
            for ep in (extra_paths or []):
                label = f"[EXT]  {os.path.basename(ep)}/"
                extra_labels.append((label, ep))
                choices.append(label)

        for name, size, item_type in all_items:
            choices.append(format_item(name, size, item_type))
        choices.append("───────────────")
        choices.append("+ Create new folder")
        choices.append("Multi-select")
        choices.append("Batch rename")
        choices.append("← Back to main menu")

        hdr = (
            f"\n  {C.BOLD}Manage Files:{C.RESET} {C.ACCENT}{current}/{C.RESET}\n"
            f"  {C.DIM}({len(dirs)} folders, {len(files)} files){C.RESET}\n"
        )
        idx = pick_option("", choices, header=hdr)
        choice = choices[idx]

        if choice == "← Back to main menu":
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
            result = ssh_run(f"mkdir -p '{new_path}'", host=host, port=port)
            if result.returncode != 0:
                error(f"Failed: {result.stderr.strip()}")
            else:
                log_action("Folder Create", f"{host}:{new_path}")
                success(f"Created: {new_path}")
        elif choice == "Multi-select":
            _multi_select_operations(current, all_items, host, base_path, extra_paths, trash_path, port=port)
        elif choice == "Batch rename":
            _batch_rename(current, host, port=port)
        else:
            extra_match = None
            for label, ep in extra_labels:
                if choice == label:
                    extra_match = ep
                    break
            if extra_match:
                current = extra_match
                continue

            has_up = 0 if _is_at_root(current, base_path, extra_paths) else 1
            item_idx = idx - has_up - len(extra_labels)
            if item_idx < 0 or item_idx >= len(all_items):
                continue
            name, size, item_type = all_items[item_idx]
            full_path = os.path.join(current, name)

            if item_type == "dir":
                hostname = local_hostname()
                action_labels = [
                    "Open folder",
                    f"Download to {hostname}",
                    "Search in folder",
                    "Search by file type",
                    "Find duplicates",
                    "Calculate size",
                    "Compress (tar.gz)",
                    "Multi-select",
                    "Rename", "Move", "Copy", "Delete", "Cancel",
                ]
                action = pick_option(
                    f"Selected folder: {name}/", action_labels,
                )
                al = action_labels[action]
                if al == "Open folder":
                    current = full_path
                elif al.startswith("Download to"):
                    _download_from_server(full_path, host, is_dir=True, port=port)
                elif al == "Search in folder":
                    _search_in_folder(full_path, host, base_path, port=port)
                elif al == "Search by file type":
                    _search_by_type(full_path, host, base_path, port=port)
                elif al == "Find duplicates":
                    _find_duplicates_in(full_path, host, port=port)
                elif al == "Calculate size":
                    _folder_size(full_path, host, port=port)
                elif al == "Compress (tar.gz)":
                    _compress_on_server(full_path, host, is_dir=True, port=port)
                elif al == "Multi-select":
                    sub_dirs, sub_files = list_remote_items(full_path, host=host, port=port)
                    _multi_select_operations(full_path, sub_dirs + sub_files, host, base_path, extra_paths, trash_path, port=port)
                elif al == "Rename":
                    _rename_remote(current, name, host, port=port)
                elif al == "Move":
                    _move_remote(full_path, host, base_path, extra_paths, port=port)
                elif al == "Copy":
                    _copy_remote(full_path, host, base_path, extra_paths, is_dir=True, port=port)
                elif al == "Delete":
                    _delete_remote(full_path, host, trash_path, is_dir=True, port=port)
            else:
                hostname = local_hostname()
                actions = [f"Download to {hostname}", "Preview"]
                lower = name.lower()
                if any(lower.endswith(ext) for ext in TEXT_EXT):
                    actions.append("Edit (nano)")
                if any(lower.endswith(ext) for ext in (
                    ".zip", ".tar", ".tar.gz", ".tgz",
                    ".tar.bz2", ".rar", ".7z",
                )):
                    actions.append("Extract on server")
                actions.extend([
                    "Checksum/Verify", "Compress (tar.gz)",
                    "Rename", "Move", "Copy", "Delete", "Cancel",
                ])

                action = pick_option(
                    f"Selected file: {name} ({size})", actions,
                )
                action_label = actions[action]

                if action_label.startswith("Download to"):
                    _download_from_server(full_path, host, is_dir=False, port=port)
                elif action_label == "Extract on server":
                    _extract_on_server(full_path, current, host, port=port)
                elif action_label == "Checksum/Verify":
                    _checksum_file(full_path, host, port=port)
                elif action_label == "Compress (tar.gz)":
                    _compress_on_server(full_path, host, port=port)
                elif action_label == "Preview":
                    _preview_file(full_path, name, host, port=port)
                elif action_label == "Edit (nano)":
                    _edit_remote(full_path, host, port=port)
                elif action_label == "Rename":
                    _rename_remote(current, name, host, port=port)
                elif action_label == "Move":
                    _move_remote(full_path, host, base_path, extra_paths, port=port)
                elif action_label == "Copy":
                    _copy_remote(full_path, host, base_path, extra_paths, is_dir=False, port=port)
                elif action_label == "Delete":
                    _delete_remote(full_path, host, trash_path, is_dir=False, port=port)


def manage_files_at(start_path, host, base_path, port=None):
    """Like manage_files but starting at a specific path."""
    current = start_path

    while True:
        dirs, files = list_remote_items(current, host=host, port=port)
        all_items = dirs + files

        choices = []
        if current != base_path:
            choices.append(".. (go up)")
        for name, size, item_type in all_items:
            choices.append(format_item(name, size, item_type))
        choices.append("← Back")

        hdr = (
            f"\n  {C.BOLD}Browse:{C.RESET} {C.ACCENT}{current}/{C.RESET}\n"
            f"  {C.DIM}({len(dirs)} folders, {len(files)} files){C.RESET}\n"
        )
        idx = pick_option("", choices, header=hdr)
        choice = choices[idx]

        if choice == "← Back":
            return
        elif choice == ".. (go up)":
            current = os.path.dirname(current)
        else:
            offset = 1 if current != base_path else 0
            item_idx = idx - offset
            if item_idx < 0 or item_idx >= len(all_items):
                continue
            name, size, item_type = all_items[item_idx]
            full_path = os.path.join(current, name)

            if item_type == "dir":
                current = full_path
            else:
                hostname = local_hostname()
                action = pick_option(
                    f"Selected: {name} ({size})",
                    [f"Download to {hostname}", "Preview", "Cancel"],
                )
                if action == 0:
                    _download_from_server(full_path, host, is_dir=False, port=port)
                elif action == 1:
                    _preview_file(full_path, name, host, port=port)


# ─── File operation helpers ─────────────────────────────────────────────────

def _rename_remote(parent_path, old_name, host, port=None):
    new_name = prompt_text(f"Rename '{old_name}' to:")
    if not new_name:
        warn("Cancelled.")
        return
    old = os.path.join(parent_path, old_name)
    new = os.path.join(parent_path, new_name)
    result = ssh_run(f"mv '{old}' '{new}'", host=host, port=port)
    if result.returncode != 0:
        error(f"Failed: {result.stderr.strip()}")
    else:
        log_action("File Rename", f"{host}:{old} → {new_name}")
        success(f"Renamed to: {new_name}")


def _move_remote(source_path, host, base_path, extra_paths=None, port=None):
    print(f"\n  {C.BOLD}Moving:{C.RESET} {source_path}")
    info("Select destination folder:")
    dest = browse_remote_folder(host, base_path, extra_paths, port=port)
    name = os.path.basename(source_path)
    result = ssh_run(f"mv '{source_path}' '{dest}/{name}'", host=host, port=port)
    if result.returncode != 0:
        error(f"Failed: {result.stderr.strip()}")
    else:
        log_action("File Move", f"{host}:{source_path} → {dest}/{name}")
        success(f"Moved to: {dest}/{name}")


def _batch_rename(current_dir, host, port=None):
    """Regex-based batch rename of files in a directory."""
    pattern = prompt_text("Find pattern (regex):")
    if not pattern:
        return
    replacement = prompt_text("Replace with:")
    if replacement is None:
        return

    result = ssh_run(f"ls -1 '{current_dir}'", host=host, port=port)
    if result.returncode != 0 or not result.stdout.strip():
        error("Could not list files.")
        return

    names = [n.strip() for n in result.stdout.strip().split("\n") if n.strip()]
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
        r = ssh_run(f"mv '{old_path}' '{new_path}'", host=host, port=port)
        if r.returncode == 0:
            ok += 1
        else:
            error(f"Failed: {old} → {new}")
    if ok > 0:
        log_action("Batch Rename", f"{ok} file(s) in {host}:{current_dir}")
    success(f"Renamed {ok}/{len(renames)} files.")


def _folder_size(remote_path, host, port=None):
    """Calculate and display the size of a remote folder."""
    name = os.path.basename(remote_path)
    info(f"Calculating size of {name}...")
    result = ssh_run(f"du -sh '{remote_path}'", host=host, port=port)
    if result.returncode != 0:
        error(f"Failed: {result.stderr.strip()}")
    else:
        size = result.stdout.strip().split("\t")[0]
        success(f"{name}: {size}")
    input("\n  Press Enter to continue...")


def _multi_select_operations(current_dir, all_items, host, base_path, extra_paths=None, trash_path=None, port=None):
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
        "Download all", "Delete all (to trash)", "Move all",
        "Compress all (tar.gz)", "Cancel",
    ])
    if action_idx == 4:
        return
    label = ["Download", "Delete", "Move", "Compress"][action_idx]
    if not confirm(f"{label} {len(items)} item(s)?"):
        warn("Cancelled.")
        return
    ok = 0
    if action_idx == 0:  # Download
        for name, size, item_type in items:
            full = os.path.join(current_dir, name)
            info(f"Downloading: {name}")
            _download_from_server(full, host, is_dir=(item_type == "dir"), port=port)
            ok += 1
    elif action_idx == 1:  # Delete to trash
        tp = trash_path or "/tmp/.homelab_trash"
        ssh_run(f"mkdir -p '{tp}'", host=host, port=port)
        for name, size, item_type in items:
            full = os.path.join(current_dir, name)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            trash_name = f"{name}_{ts}"
            r = ssh_run(f"mv '{full}' '{tp}/{trash_name}'", host=host, port=port)
            if r.returncode == 0:
                ssh_run(f"echo '{full}' > '{tp}/{trash_name}.origin'", host=host, port=port)
                ok += 1
    elif action_idx == 2:  # Move
        info("Select destination folder:")
        dest = browse_remote_folder(host, base_path, extra_paths, port=port)
        for name, size, item_type in items:
            full = os.path.join(current_dir, name)
            r = ssh_run(f"mv '{full}' '{dest}/{name}'", host=host, port=port)
            if r.returncode == 0:
                ok += 1
    elif action_idx == 3:  # Compress all into one archive
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"batch_{ts}.tar.gz"
        names = " ".join(f"'{item[0]}'" for item in items)
        info(f"Compressing {len(items)} items...")
        r = ssh_run(f"cd '{current_dir}' && tar -czf '{archive_name}' {names}", host=host, port=port)
        if r.returncode == 0:
            success(f"Created: {archive_name}")
            return
        else:
            error(f"Compression failed: {r.stderr.strip()}")
            return
    success(f"Completed: {ok}/{len(items)} successful")


def _copy_remote(source_path, host, base_path, extra_paths=None, is_dir=False, port=None):
    """Copy a file or folder to another location on the server."""
    name = os.path.basename(source_path)
    print(f"\n  {C.BOLD}Copying:{C.RESET} {name}")
    info("Select destination folder:")
    dest = browse_remote_folder(host, base_path, extra_paths, port=port)
    if is_dir:
        result = ssh_run(f"cp -r '{source_path}' '{dest}/{name}'", host=host, port=port)
    else:
        result = ssh_run(f"cp '{source_path}' '{dest}/{name}'", host=host, port=port)
    if result.returncode != 0:
        error(f"Failed: {result.stderr.strip()}")
    else:
        log_action("File Copy", f"{host}:{source_path} → {dest}/{name}")
        success(f"Copied to: {dest}/{name}")


def _edit_remote(remote_path, host, port=None):
    """Open a remote file in nano over SSH with TTY."""
    subprocess.run(["ssh", "-t"] + (["-p", str(port)] if port else []) + [host, f"nano '{remote_path}'"])


def _delete_remote(path, host, trash_path=None, is_dir=False, port=None):
    name = os.path.basename(path)
    if CFG.get("dry_run"):
        info(f"[DRY RUN] Would delete: {path}")
        return

    tp = trash_path or "/tmp/.homelab_trash"
    action = pick_option(
        f"Delete '{name}'?",
        ["Move to trash", "Permanently delete", "Cancel"],
    )

    if action == 2:
        warn("Cancelled.")
        return
    elif action == 0:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        trash_name = f"{name}_{ts}"
        ssh_run(f"mkdir -p '{tp}'", host=host, port=port)
        result = ssh_run(f"mv '{path}' '{tp}/{trash_name}'", host=host, port=port)
        if result.returncode != 0:
            error(f"Failed: {result.stderr.strip()}")
        else:
            ssh_run(f"echo '{path}' > '{tp}/{trash_name}.origin'", host=host, port=port)
            log_action("File Move to Trash", f"{host}:{path}")
            success(f"Moved to trash: {name}")
    else:
        if not confirm("Permanently delete? This cannot be undone.", default_yes=False):
            warn("Cancelled.")
            return
        if is_dir:
            result = ssh_run(f"rm -rf '{path}'", host=host, port=port)
        else:
            result = ssh_run(f"rm -f '{path}'", host=host, port=port)
        if result.returncode != 0:
            error(f"Failed: {result.stderr.strip()}")
        else:
            log_action("File Delete", f"{host}:{path}")
            success(f"Deleted: {name}")


def manage_trash(host, trash_path=None, port=None):
    """Browse and manage items in the trash folder."""
    tp = trash_path or "/tmp/.homelab_trash"
    while True:
        result = ssh_run(
            f"ls -1 '{tp}' 2>/dev/null | grep -v '\\.origin$'",
            host=host, port=port,
        )
        if result.returncode != 0 or not result.stdout.strip():
            info("Trash is empty.")
            return

        items = [
            i.strip() for i in result.stdout.strip().split("\n")
            if i.strip()
        ]
        choices = items + ["Empty all trash", "← Back"]
        idx = pick_option("Trash:", choices)

        if choices[idx] == "← Back":
            return
        elif choices[idx] == "Empty all trash":
            if confirm("Permanently delete ALL items in trash?", default_yes=False):
                ssh_run(f"rm -rf '{tp}'/*", host=host, port=port)
                log_action("Trash Empty", f"{host}:{tp}")
                success("Trash emptied.")
            return
        else:
            trash_item = items[idx]
            full = f"{tp}/{trash_item}"
            origin_result = ssh_run(f"cat '{full}.origin' 2>/dev/null", host=host, port=port)
            orig = origin_result.stdout.strip() if origin_result.returncode == 0 else ""

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
                ssh_run(f"mkdir -p '{dest_dir}'", host=host, port=port)
                r = ssh_run(f"mv '{full}' '{orig}'", host=host, port=port)
                if r.returncode == 0:
                    ssh_run(f"rm -f '{full}.origin'", host=host, port=port)
                    log_action("Trash Restore", f"{host}:{orig}")
                    success(f"Restored: {orig}")
                else:
                    error(f"Failed: {r.stderr.strip()}")
            elif action == 1:
                r = ssh_run(f"rm -rf '{full}'", host=host, port=port)
                if r.returncode == 0:
                    ssh_run(f"rm -f '{full}.origin'", host=host, port=port)
                    log_action("File Delete", f"{host}:{full} (from trash)")
                    success(f"Permanently deleted: {trash_item}")
                else:
                    error(f"Failed: {r.stderr.strip()}")


# ─── Preview / Extract / Compress / Checksum ────────────────────────────────

def _preview_file(remote_path, name, host, port=None):
    """Preview a remote file based on its type."""
    lower = name.lower()

    if any(lower.endswith(ext) for ext in TEXT_EXT):
        print(f"\n  {C.BOLD}Preview: {name} (first 40 lines){C.RESET}\n")
        result = ssh_run(f"head -40 '{remote_path}'", host=host, port=port)
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                print(f"  {C.DIM}│{C.RESET} {line}")
        else:
            error("Could not read file.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    img_ext = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp")
    if any(lower.endswith(ext) for ext in img_ext):
        print(f"\n  {C.BOLD}Image info: {name}{C.RESET}\n")
        result = ssh_run(f"file '{remote_path}' && stat -c 'Size: %s bytes' '{remote_path}' 2>/dev/null || stat -f 'Size: %z bytes' '{remote_path}'", host=host, port=port)
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        result2 = ssh_run(f"identify '{remote_path}' 2>/dev/null", host=host, port=port)
        if result2.returncode == 0 and result2.stdout.strip():
            print(f"  {result2.stdout.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    media_ext = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
                 ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a", ".m4v")
    if any(lower.endswith(ext) for ext in media_ext):
        print(f"\n  {C.BOLD}Media info: {name}{C.RESET}\n")
        result = ssh_run(f"mediainfo '{remote_path}' 2>/dev/null | head -30", host=host, port=port)
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        else:
            result = ssh_run(f"ffprobe -hide_banner '{remote_path}' 2>&1 | head -20", host=host, port=port)
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    print(f"  {line}")
            else:
                result = ssh_run(f"file '{remote_path}'", host=host, port=port)
                if result.returncode == 0:
                    print(f"  {result.stdout.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}File info: {name}{C.RESET}\n")
    result = ssh_run(f"file '{remote_path}' && ls -lh '{remote_path}'", host=host, port=port)
    if result.returncode == 0:
        for line in result.stdout.strip().split("\n"):
            print(f"  {line}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _extract_on_server(archive_path, current_dir, host, port=None):
    """Extract an archive on the remote server."""
    name = os.path.basename(archive_path)
    lower = name.lower()

    extract_to = prompt_text(f"Extract to [{current_dir}]:") or current_dir

    if CFG.get("dry_run"):
        info(f"[DRY RUN] Would extract {name} to {extract_to}")
        return

    info(f"Extracting {name}...")

    if lower.endswith(".zip"):
        cmd = f"unzip -o '{archive_path}' -d '{extract_to}'"
    elif lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        cmd = f"tar -xzf '{archive_path}' -C '{extract_to}'"
    elif lower.endswith(".tar.bz2"):
        cmd = f"tar -xjf '{archive_path}' -C '{extract_to}'"
    elif lower.endswith(".tar"):
        cmd = f"tar -xf '{archive_path}' -C '{extract_to}'"
    elif lower.endswith(".rar"):
        cmd = f"unrar x '{archive_path}' '{extract_to}/'"
    elif lower.endswith(".7z"):
        cmd = f"7z x '{archive_path}' -o'{extract_to}'"
    else:
        error("Unsupported archive format.")
        return

    result = ssh_run(cmd, host=host, port=port)
    if result.returncode != 0:
        error(f"Extraction failed: {result.stderr.strip()}")
    else:
        log_action("File Extract", f"{host}:{archive_path} → {extract_to}")
        success(f"Extracted to: {extract_to}")


def _compress_on_server(path, host, is_dir=False, port=None):
    """Compress a file or folder on the server as tar.gz."""
    name = os.path.basename(path)
    parent = os.path.dirname(path)
    default_name = f"{name}.tar.gz"
    archive_name = prompt_text(f"Archive name [{default_name}]:") or default_name
    if not archive_name.endswith((".tar.gz", ".tgz")):
        archive_name += ".tar.gz"
    archive_path = os.path.join(parent, archive_name)
    if CFG.get("dry_run"):
        info(f"[DRY RUN] Would compress {name} → {archive_name}")
        return
    info(f"Compressing {name}...")
    result = ssh_run(f"tar -czf '{archive_path}' -C '{parent}' '{name}'", host=host, port=port)
    if result.returncode != 0:
        error(f"Compression failed: {result.stderr.strip()}")
    else:
        size_r = ssh_run(f"stat -c '%s' '{archive_path}' 2>/dev/null", host=host, port=port)
        if size_r.returncode == 0:
            sz = int(size_r.stdout.strip())
            if sz > 1_000_000_000:
                label = f"{sz / 1_000_000_000:.1f} GB"
            elif sz > 1_000_000:
                label = f"{sz / 1_000_000:.1f} MB"
            else:
                label = f"{sz / 1_000:.1f} KB"
            success(f"Created: {archive_name} ({label})")
        else:
            success(f"Created: {archive_name}")


def _checksum_file(remote_path, host, port=None):
    """Calculate or verify file checksums."""
    name = os.path.basename(remote_path)
    idx = pick_option(f"Checksum for {name}:", [
        "MD5", "SHA256", "Both", "Verify against hash", "Cancel",
    ])
    if idx == 4:
        return
    print()
    hashes = {}
    if idx in (0, 2):
        info("Computing MD5...")
        r = ssh_run(f"md5sum '{remote_path}'", host=host, port=port)
        if r.returncode == 0:
            h = r.stdout.strip().split()[0]
            hashes["MD5"] = h
            print(f"  {C.BOLD}MD5:{C.RESET}    {C.ACCENT}{h}{C.RESET}")
        else:
            error("MD5 failed.")
    if idx in (1, 2):
        info("Computing SHA256...")
        r = ssh_run(f"sha256sum '{remote_path}'", host=host, port=port)
        if r.returncode == 0:
            h = r.stdout.strip().split()[0]
            hashes["SHA256"] = h
            print(f"  {C.BOLD}SHA256:{C.RESET} {C.ACCENT}{h}{C.RESET}")
        else:
            error("SHA256 failed.")
    if idx == 3:
        expected = prompt_text("Enter expected hash (MD5 or SHA256):").strip().lower()
        if not expected:
            return
        if len(expected) == 32:
            cmd, label = "md5sum", "MD5"
        elif len(expected) == 64:
            cmd, label = "sha256sum", "SHA256"
        else:
            error("Invalid hash length. MD5=32 chars, SHA256=64 chars.")
            input("\n  Press Enter to continue...")
            return
        info(f"Computing {label}...")
        r = ssh_run(f"{cmd} '{remote_path}'", host=host, port=port)
        if r.returncode == 0:
            actual = r.stdout.strip().split()[0].lower()
            if actual == expected:
                success(f"{label} verification: MATCH")
            else:
                error(f"{label} verification: MISMATCH")
                print(f"  Expected: {expected}")
                print(f"  Actual:   {actual}")
        else:
            error("Checksum failed.")
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


def _compress_and_download(remote_path, host, port=None):
    """Compress a folder on the server, download the archive, then clean up."""
    name = os.path.basename(remote_path)
    archive_name = f"{name}.tar.gz"
    archive_path = f"/tmp/{archive_name}"

    info(f"Compressing {name} on server...")
    result = ssh_run(f"tar -czf '{archive_path}' -C '{os.path.dirname(remote_path)}' '{name}'", host=host, port=port)
    if result.returncode != 0:
        error(f"Compression failed: {result.stderr.strip()}")
        return

    size_result = ssh_run(f"ls -lh '{archive_path}' | awk '{{print $5}}'", host=host, port=port)
    if size_result.returncode == 0:
        info(f"Compressed size: {size_result.stdout.strip()}")

    default_dest = os.path.expanduser(CFG["default_download_dir"])
    dest = prompt_text(f"Local destination [{default_dest}]:") or default_dest
    dest = os.path.expanduser(dest)

    if not os.path.isdir(dest):
        error(f"Directory does not exist: {dest}")
        ssh_run(f"rm -f '{archive_path}'", host=host, port=port)
        return

    start = time.time()
    source = f"{host}:{archive_path}"
    result = rsync_transfer(source, f"{dest}/", is_dir=False, port=port)
    elapsed = time.time() - start

    ssh_run(f"rm -f '{archive_path}'", host=host, port=port)

    if result.returncode != 0:
        error("Download failed.")
    else:
        success(f"Downloaded: {dest}/{archive_name} ({elapsed:.1f}s)")
        notify("Homelab", f"Download complete: {archive_name}")
        log_action("File Download", f"{host}:{remote_path} → {dest} (compressed)")
        log_transfer("download", "compress+rsync", remote_path, dest, 1)


# ─── 5. Download from Server ───────────────────────────────────────────────

def download_from_server(host, base_path, port=None):
    current = base_path
    info(f"Browse {host} to download files/folders")

    while True:
        dirs, files = list_remote_items(current, host=host, port=port)
        all_items = dirs + files

        choices = []
        if current != base_path:
            choices.append(".. (go up)")
        for name, size, item_type in all_items:
            choices.append(format_item(name, size, item_type))
        choices.append("← Back to main menu")

        hdr = f"\n  {C.BOLD}Download from Server:{C.RESET} {C.ACCENT}{current}/{C.RESET}\n"
        idx = pick_option("Select item to download:", choices, header=hdr)
        choice = choices[idx]

        if choice == "← Back to main menu":
            return
        elif choice == ".. (go up)":
            current = os.path.dirname(current)
        else:
            offset = 1 if current != base_path else 0
            item_idx = idx - offset
            if item_idx < 0 or item_idx >= len(all_items):
                continue
            name, size, item_type = all_items[item_idx]
            full_path = os.path.join(current, name)

            if item_type == "dir":
                action = pick_option(
                    f"Selected folder: {name}/",
                    ["Open folder", "Download entire folder", "Compress & download (tar.gz)", "Cancel"],
                )
                if action == 0:
                    current = full_path
                elif action == 1:
                    _download_from_server(full_path, host, is_dir=True, port=port)
                elif action == 2:
                    _compress_and_download(full_path, host, port=port)
            else:
                action = pick_option(
                    f"Selected file: {name} ({size})",
                    ["Download", "Preview", "Cancel"],
                )
                if action == 0:
                    _download_from_server(full_path, host, is_dir=False, port=port)
                elif action == 1:
                    _preview_file(full_path, name, host, port=port)


def _download_from_server(remote_path, host, is_dir=False, port=None):
    default_dest = os.path.expanduser(CFG["default_download_dir"])
    dest = prompt_text(f"Local destination [{default_dest}]:") or default_dest
    dest = os.path.expanduser(dest)

    if not os.path.isdir(dest):
        error(f"Directory does not exist: {dest}")
        return

    name = os.path.basename(remote_path)
    print(f"\n  {C.BOLD}Downloading:{C.RESET} {name}")
    print(f"  {C.BOLD}From:{C.RESET} {C.ACCENT}{host}:{remote_path}{C.RESET}")
    print(f"  {C.BOLD}To:{C.RESET}   {C.ACCENT}{dest}/{C.RESET}")

    dl_source = f"{host}:{remote_path}"
    extra_args = pick_rsync_options(source=dl_source, dest=f"{dest}/", port=port)

    start = time.time()
    result = rsync_transfer(dl_source, f"{dest}/", is_dir=is_dir, port=port, extra_args=extra_args)
    elapsed = time.time() - start
    if result.returncode != 0:
        error("Download failed.")
        notify("Homelab", f"Download failed: {name}")
    else:
        success(f"Downloaded: {dest}/{name} ({elapsed:.1f}s)")
        notify("Homelab", f"Download complete: {name}")
        log_action("File Download", f"{host}:{remote_path} → {dest}")
        log_transfer("download", "rsync", remote_path, dest, 1)


# ─── Search (integrated into file manager) ─────────────────────────────────

def _search_in_folder(folder_path, host, base_path, port=None):
    """Search by filename or content within a folder."""
    mode = pick_option(
        "Search mode:",
        ["By filename", "By content (grep)", "Cancel"],
    )
    if mode == 2:
        return

    pattern = prompt_text("Search pattern:")
    if not pattern:
        warn("No pattern entered.")
        return

    info(f"Searching in {folder_path}...")

    if mode == 0:
        result = ssh_run(f"find '{folder_path}' -iname '*{pattern}*' 2>/dev/null | head -50", host=host, port=port)
    else:
        result = ssh_run(f"grep -r -l -i '{pattern}' '{folder_path}' 2>/dev/null | head -50", host=host, port=port)

    if result.returncode != 0 or not result.stdout.strip():
        warn("No results found.")
        return

    matches = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
    success(f"Found {len(matches)} result(s):")

    choices = []
    for m in matches:
        rel = m.replace(base_path, "~", 1)
        choices.append(rel)
    choices.append("← Back")

    idx = pick_option("Select a result:", choices)
    if idx >= len(matches):
        return

    selected = matches[idx]
    is_dir_check = ssh_run(f"test -d '{selected}' && echo dir || echo file", host=host, port=port)
    is_dir = is_dir_check.stdout.strip() == "dir"

    hostname = local_hostname()
    action = pick_option(
        f"Selected: {selected}",
        [f"Download to {hostname}", "Cancel"],
    )
    if action == 0:
        _download_from_server(selected, host, is_dir=is_dir, port=port)


def _search_by_type(folder_path, host, base_path, port=None):
    """Search for files by extension within a folder."""
    ext = prompt_text("File extension (e.g. .mkv, .jpg):")
    if not ext:
        return
    if not ext.startswith("."):
        ext = "." + ext

    info(f"Searching for *{ext} in {folder_path}...")
    result = ssh_run(f"find '{folder_path}' -iname '*{ext}' 2>/dev/null | head -50", host=host, port=port)

    if result.returncode != 0 or not result.stdout.strip():
        warn("No results found.")
        return

    matches = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
    success(f"Found {len(matches)} result(s):")

    choices = []
    for m in matches:
        rel = m.replace(base_path, "~", 1)
        choices.append(rel)
    choices.append("← Back")

    idx = pick_option("Select a result:", choices)
    if idx >= len(matches):
        return

    selected = matches[idx]
    hostname = local_hostname()
    action = pick_option(
        f"Selected: {selected}",
        [f"Download to {hostname}", "Preview", "Cancel"],
    )
    if action == 0:
        _download_from_server(selected, host, is_dir=False, port=port)
    elif action == 1:
        _preview_file(selected, os.path.basename(selected), host, port=port)


# ─── Find Duplicates (scoped to folder) ────────────────────────────────────

def _find_duplicates_in(scan_path, host, port=None):
    """Scan a path for duplicate files by size + partial hash."""
    info("Scanning for files with identical sizes...")
    result = ssh_run(
        f"find '{scan_path}' -type f -printf '%s %p\\n' 2>/dev/null | sort -n",
        host=host, port=port,
    )
    if result.returncode != 0 or not result.stdout.strip():
        info("No files found or scan failed.")
        return

    size_groups = {}
    for line in result.stdout.strip().split("\n"):
        parts = line.strip().split(" ", 1)
        if len(parts) != 2:
            continue
        size, path = parts
        size_groups.setdefault(size, []).append(path)

    candidates = {
        s: paths for s, paths in size_groups.items()
        if len(paths) > 1 and int(s) > 0
    }

    if not candidates:
        info("No duplicate candidates found.")
        return

    info(f"Found {len(candidates)} size group(s) with potential duplicates.")
    info("Checking partial hashes...")

    dup_groups = []
    for size, paths in candidates.items():
        hash_map = {}
        for p in paths:
            hr = ssh_run(f"head -c 4096 '{p}' | md5sum", host=host, port=port)
            if hr.returncode == 0:
                h = hr.stdout.strip().split()[0]
                hash_map.setdefault(h, []).append(p)
        for h, group in hash_map.items():
            if len(group) > 1:
                dup_groups.append((size, group))

    if not dup_groups:
        success("No duplicates found!")
        return

    total = sum(len(g) - 1 for _, g in dup_groups)
    print(f"\n  {C.BOLD}Found {len(dup_groups)} duplicate group(s) "
          f"({total} extra files):{C.RESET}")

    for size, group in dup_groups:
        sz_mb = int(size) / 1_000_000
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
                r = ssh_run(f"rm -f '{p}'", host=host, port=port)
                if r.returncode == 0:
                    success(f"Deleted: {os.path.basename(p)}")
                else:
                    error(f"Failed: {p}")
        elif action == 2:
            return

    success("Duplicate scan complete.")


# ─── Bookmarks ──────────────────────────────────────────────────────────────

def manage_bookmarks():
    while True:
        bookmarks = CFG["bookmarks"]
        if not bookmarks:
            warn("No bookmarks saved yet.")
            info("Bookmarks can be saved while browsing folders.")
            return

        print(f"\n  {C.BOLD}Bookmarked Folders:{C.RESET}\n")
        choices = []
        for bm in bookmarks:
            choices.append(f"★ {bm}")
        choices.append("+ Add bookmark")
        choices.append("← Back")

        idx = pick_option("", choices)

        if idx == len(bookmarks) + 1:
            return
        elif idx == len(bookmarks):
            path = prompt_text("Path to bookmark:")
            if path and path not in CFG["bookmarks"]:
                CFG["bookmarks"].append(path)
                save_config(CFG)
                success(f"Bookmarked: {path}")
        else:
            action = pick_option(
                f"Bookmark: {bookmarks[idx]}",
                ["Remove bookmark", "Cancel"],
            )
            if action == 0:
                removed = CFG["bookmarks"].pop(idx)
                save_config(CFG)
                success(f"Removed: {removed}")


# ─── History viewer ─────────────────────────────────────────────────────────

def show_history(host=None, port=None):
    from homelab.history import load_history, save_history
    history = load_history()
    if not history:
        warn("No transfer history yet.")
        return

    recent = list(reversed(history[-20:]))
    print(f"\n  {C.BOLD}Recent Transfers:{C.RESET}\n")

    choices = []
    for entry in recent:
        ts = entry.get("timestamp", "?")[:19].replace("T", " ")
        direction = entry.get("direction", "?")
        method = entry.get("method", "?")
        url = entry.get("url", "")
        dest = entry.get("destination", "")
        count = entry.get("item_count", 0)

        if direction == "fetch_transfer":
            icon = "↓↑"
        elif direction == "upload":
            icon = "↑ "
        elif direction == "download":
            icon = "↓ "
        elif direction == "queue_transfer":
            icon = "⇊ "
        else:
            icon = "  "

        url_short = url if len(url) <= 40 else "..." + url[-37:]
        choices.append(f"{icon} {ts}  {method}  {url_short} → {dest}  ({count} items)")

    choices.append("Clear history")
    choices.append("← Back")

    idx = pick_option("Select to re-transfer, or go back:", choices)

    if idx == len(recent) + 1:
        return
    elif idx == len(recent):
        if confirm("Clear all transfer history?", default_yes=False):
            save_history([])
            success("History cleared.")
        return

    entry = recent[idx]
    direction = entry.get("direction", "")
    method = entry.get("method", "")
    url = entry.get("url", "")
    dest = entry.get("destination", "")

    print(f"\n  {C.BOLD}Re-transfer:{C.RESET}")
    print(f"  {C.BOLD}Method:{C.RESET}      {method}")
    print(f"  {C.BOLD}URL/Source:{C.RESET}  {url}")
    print(f"  {C.BOLD}Destination:{C.RESET} {dest}")

    if not confirm("Re-run this transfer?"):
        return

    if direction == "fetch_transfer" and url and host:
        tmpdir = tempfile.mkdtemp(prefix="homelab_")
        try:
            tool = None
            for name, t in FETCH_METHODS:
                if name == method:
                    tool = t
                    break
            if tool is None:
                tool = method.lower()

            if fetch_files(method, tool, url, tmpdir):
                if transfer_to_server(tmpdir, dest, host, port=port):
                    items = os.listdir(tmpdir)
                    log_transfer("fetch_transfer", method, url, dest, len(items))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    elif direction == "upload" and url and host:
        if os.path.exists(url):
            is_dir = os.path.isdir(url)
            rsync_dest = f"{host}:{dest}/"
            result = rsync_transfer(url, rsync_dest, is_dir=is_dir, port=port)
            if result.returncode == 0:
                success("Re-upload complete!")
                log_transfer("upload", "rsync", url, dest, 1)
            else:
                error("Upload failed.")
        else:
            error(f"Local file no longer exists: {url}")
    else:
        warn("Cannot automatically re-run this transfer type. Use the appropriate menu option.")


# ─── Mount Browser ──────────────────────────────────────────────────────────

def mount_browser(host, base_path="/mnt/user/", port=None):
    """Tree view of shares with sizes."""
    info("Scanning share sizes (this may take a moment)...")
    result = ssh_run(
        f"du -h --max-depth=2 '{base_path}' 2>/dev/null | sort -rh | head -50",
        host=host, port=port,
    )
    if result.returncode != 0 or not result.stdout.strip():
        error("Could not scan mounts.")
        return

    lines = result.stdout.strip().split("\n")
    entries = []
    for line in lines:
        parts = line.strip().split("\t", 1)
        if len(parts) == 2:
            size, path = parts
            entries.append((size.strip(), path.strip()))

    if not entries:
        info("No data returned.")
        return

    choices = []
    for size, path in entries:
        rel = path.replace(base_path, "").rstrip("/")
        depth = rel.count("/") if rel else 0
        indent = "  " * depth
        display = rel.split("/")[-1] if rel else base_path.rstrip("/")
        choices.append(f"{size:>8}  {indent}{display}/")
    choices.append("← Back")

    idx = pick_option("Mount Browser:", choices)
    if idx < len(entries):
        _, selected_path = entries[idx]
        action = pick_option(
            f"Selected: {selected_path}",
            ["Open in file manager", "Cancel"],
        )
        if action == 0:
            manage_files_at(selected_path, host, base_path, port=port)
