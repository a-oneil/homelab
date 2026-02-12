"""Watch Folder — monitor local directories and auto-upload new files."""

import os
import time
import threading

from homelab.modules.auditlog import log_action
from homelab.config import CFG, save_config
from homelab.modules.transport import rsync_transfer
from homelab.ui import (
    C, pick_option, prompt_text, info, success, error, warn,
)


_WATCHER_THREAD = None
_WATCHER_STOP = threading.Event()


def watch_folder_menu():
    """Manage watch folders that auto-upload to the server."""
    while True:
        watches = CFG.get("watch_folders", [])
        running = _WATCHER_THREAD is not None and _WATCHER_THREAD.is_alive()

        print(f"\n  {C.BOLD}Watch Folders{C.RESET}")
        print(f"  Status: {C.GREEN}Running{C.RESET}" if running else f"  Status: {C.DIM}Stopped{C.RESET}")

        if watches:
            print()
            for i, w in enumerate(watches):
                local = w.get("local", "?")
                remote = w.get("remote", "?")
                host = w.get("host", "?")
                print(f"    {i + 1}. {local} → {host}:{remote}")
        else:
            print(f"  {C.DIM}No watch folders configured.{C.RESET}")

        choices = ["+ Add watch folder", "Remove watch folder"]
        if running:
            choices.append("Stop watcher")
        else:
            choices.append("Start watcher")
        choices.extend(["Test sync now"])
        choices.sort()
        choices.append("← Back")

        idx = pick_option("", choices)
        choice = choices[idx]

        if choice == "← Back":
            return
        elif choice == "+ Add watch folder":
            _add_watch()
        elif choice == "Remove watch folder":
            _remove_watch()
        elif choice == "Start watcher":
            _start_watcher()
        elif choice == "Stop watcher":
            _stop_watcher()
        elif choice == "Test sync now":
            _sync_all_now()


def _add_watch():
    """Add a new watch folder."""
    local = prompt_text("Local folder to watch (e.g. ~/Desktop/uploads):")
    if not local:
        return
    local = os.path.expanduser(local)
    if not os.path.isdir(local):
        error(f"Not a directory: {local}")
        return

    host = prompt_text("SSH host (e.g. root@10.0.0.5):")
    if not host:
        return

    remote = prompt_text("Remote destination (e.g. /mnt/user/incoming):")
    if not remote:
        return

    watches = CFG.get("watch_folders", [])
    watches.append({"local": local, "remote": remote, "host": host})
    CFG["watch_folders"] = watches
    save_config(CFG)
    log_action("Watch Folder Add", f"{local} → {host}:{remote}")
    success(f"Watch folder added: {local} → {remote}")


def _remove_watch():
    """Remove a watch folder."""
    watches = CFG.get("watch_folders", [])
    if not watches:
        warn("No watch folders to remove.")
        return

    choices = [f"{w['local']} → {w.get('host', '?')}:{w['remote']}" for w in watches]
    choices.append("Cancel")
    idx = pick_option("Remove which?", choices)
    if idx >= len(watches):
        return

    removed = watches.pop(idx)
    CFG["watch_folders"] = watches
    save_config(CFG)
    log_action("Watch Folder Remove", removed['local'])
    success(f"Removed: {removed['local']}")


def _start_watcher():
    """Start the background watcher thread."""
    global _WATCHER_THREAD
    if _WATCHER_THREAD and _WATCHER_THREAD.is_alive():
        warn("Watcher is already running.")
        return

    watches = CFG.get("watch_folders", [])
    if not watches:
        warn("No watch folders configured. Add one first.")
        return

    _WATCHER_STOP.clear()
    _WATCHER_THREAD = threading.Thread(target=_watcher_loop, daemon=True)
    _WATCHER_THREAD.start()
    log_action("Watcher Start", f"{len(watches)} watch folder(s)")
    success("Watcher started in background.")


def _stop_watcher():
    """Stop the background watcher thread."""
    _WATCHER_STOP.set()
    log_action("Watcher Stop", "")
    success("Watcher stopped.")


def _watcher_loop():
    """Background loop that checks for new files and uploads them."""
    # Track known files per watch folder
    known_files = {}
    watches = CFG.get("watch_folders", [])

    # Initial scan to establish baseline
    for w in watches:
        local = w.get("local", "")
        if os.path.isdir(local):
            known_files[local] = set(os.listdir(local))

    while not _WATCHER_STOP.is_set():
        _WATCHER_STOP.wait(timeout=10)  # Check every 10 seconds
        if _WATCHER_STOP.is_set():
            break

        watches = CFG.get("watch_folders", [])
        for w in watches:
            local = w.get("local", "")
            remote = w.get("remote", "")
            host = w.get("host", "")
            port = w.get("port") or None
            if not os.path.isdir(local) or not host:
                continue

            current = set(os.listdir(local))
            previous = known_files.get(local, set())
            new_files = current - previous

            for fname in new_files:
                if fname.startswith("."):
                    continue
                filepath = os.path.join(local, fname)
                # Wait a moment for the file to finish writing
                time.sleep(2)
                if not os.path.exists(filepath):
                    continue

                is_dir = os.path.isdir(filepath)
                dest = f"{host}:{remote}/"
                try:
                    result = rsync_transfer(filepath, dest, is_dir=is_dir, port=port)
                    if result.returncode == 0:
                        from homelab.notifications import notify
                        notify("Homelab", f"Auto-uploaded: {fname}")
                except Exception:
                    pass

            known_files[local] = current


def _sync_all_now():
    """Manually sync all watch folders right now."""
    watches = CFG.get("watch_folders", [])
    if not watches:
        warn("No watch folders configured.")
        return

    for w in watches:
        local = w.get("local", "")
        remote = w.get("remote", "")
        host = w.get("host", "")
        port = w.get("port") or None
        if not os.path.isdir(local):
            warn(f"Skipping (not found): {local}")
            continue
        if not host:
            warn(f"Skipping (no host configured): {local}")
            continue

        files = [f for f in os.listdir(local) if not f.startswith(".")]
        if not files:
            info(f"No files in {local}")
            continue

        info(f"Syncing {len(files)} items from {local}...")
        for fname in files:
            filepath = os.path.join(local, fname)
            is_dir = os.path.isdir(filepath)
            dest = f"{host}:{remote}/"
            result = rsync_transfer(filepath, dest, is_dir=is_dir, port=port)
            if result.returncode == 0:
                success(f"  Uploaded: {fname}")
            else:
                error(f"  Failed: {fname}")

    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
