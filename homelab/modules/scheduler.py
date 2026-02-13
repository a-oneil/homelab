"""Scheduled Tasks — cron-like scheduler for recurring homelab actions."""

import threading
import time
from datetime import datetime

from homelab.config import CFG, save_config
from homelab.modules.auditlog import log_action
from homelab.ui import C, pick_option, confirm, success, info


# Background scheduler thread
_scheduler_thread = None
_stop_event = threading.Event()

# Available task types and their handlers
TASK_TYPES = {
    "speedtest": "Run speed test",
    "container_updates": "Check container updates",
    "health_check": "Run health check",
    "backup_config": "Backup config file",
}

# Interval presets in seconds
INTERVAL_PRESETS = [
    ("Every 15 minutes", 900),
    ("Every 30 minutes", 1800),
    ("Every hour", 3600),
    ("Every 6 hours", 21600),
    ("Every 12 hours", 43200),
    ("Every 24 hours", 86400),
]


def _get_tasks():
    """Get scheduled tasks from config."""
    return CFG.get("scheduled_tasks", [])


def _save_tasks(tasks):
    """Save scheduled tasks to config."""
    CFG["scheduled_tasks"] = tasks
    save_config(CFG)


def _run_task(task):
    """Execute a single scheduled task."""
    task_type = task.get("type", "")
    try:
        if task_type == "speedtest":
            from homelab.plugins.speedtest import _run_speed_test
            _run_speed_test(silent=True)
        elif task_type == "container_updates":
            from homelab.modules.containerupdates import _get_docker_hosts, _check_host
            for h in _get_docker_hosts():
                _check_host(h["host"], h.get("port"))
        elif task_type == "health_check":
            from homelab.modules.healthmonitor import get_health_alerts
            get_health_alerts([])
        elif task_type == "backup_config":
            import json
            import os
            backup_dir = os.path.join(os.path.expanduser("~/.homelab"), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"homelabrc_{stamp}.json")
            with open(backup_path, "w") as f:
                json.dump(CFG, f, indent=2)
        task["last_run"] = datetime.now().isoformat()
        task["run_count"] = task.get("run_count", 0) + 1
        _save_tasks(_get_tasks())
    except Exception:
        pass  # Silent failure for background tasks


def _scheduler_loop():
    """Background loop that checks and runs due tasks."""
    from homelab.ui import suppress_output
    suppress_output(True)
    while not _stop_event.is_set():
        tasks = _get_tasks()
        now = time.time()
        for task in tasks:
            if not task.get("enabled", True):
                continue
            interval = task.get("interval", 3600)
            last_run = task.get("last_run", "")
            if last_run:
                try:
                    last_dt = datetime.fromisoformat(last_run)
                    elapsed = now - last_dt.timestamp()
                except (ValueError, TypeError):
                    elapsed = interval + 1
            else:
                elapsed = interval + 1

            if elapsed >= interval:
                _run_task(task)
        _stop_event.wait(60)  # Check every 60 seconds


def start_scheduler():
    """Start the background scheduler thread."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    tasks = _get_tasks()
    if not any(t.get("enabled", True) for t in tasks):
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    """Stop the background scheduler thread."""
    _stop_event.set()


def scheduler_menu():
    """Interactive menu for managing scheduled tasks."""
    while True:
        tasks = _get_tasks()

        # Build header
        enabled_count = sum(1 for t in tasks if t.get("enabled", True))
        running = _scheduler_thread and _scheduler_thread.is_alive()
        status_str = f"{C.GREEN}Running{C.RESET}" if running else f"{C.DIM}Stopped{C.RESET}"
        hdr = (
            f"\n  {C.BOLD}Scheduled Tasks{C.RESET}\n"
            f"  Scheduler: {status_str}  |  "
            f"{enabled_count} active task(s)\n"
        )

        choices = []
        for i, task in enumerate(tasks):
            ttype = TASK_TYPES.get(task.get("type", ""), task.get("type", "?"))
            enabled = task.get("enabled", True)
            interval = task.get("interval", 3600)
            interval_str = _format_interval(interval)
            last_run = task.get("last_run", "")
            last_str = last_run[:19] if last_run else "never"
            icon = f"{C.GREEN}●{C.RESET}" if enabled else f"{C.DIM}○{C.RESET}"
            choices.append(f"{icon} {ttype:<28} every {interval_str:<12} last: {C.DIM}{last_str}{C.RESET}")

        if not choices:
            choices.append(f"{C.DIM}No scheduled tasks configured{C.RESET}")

        choices.append("───────────────")
        choices.append("+ Add Task")
        if tasks:
            choices.append("Clear All Tasks")
        choices.append("← Back")

        idx = pick_option("Scheduled Tasks:", choices, header=hdr)

        # Handle back
        if idx == len(choices) - 1:
            return
        # Handle separator
        elif choices[idx].startswith("───"):
            continue
        # Handle "no tasks" placeholder
        elif "No scheduled tasks" in choices[idx]:
            continue
        # Handle Clear All
        elif choices[idx] == "Clear All Tasks":
            if confirm("Remove all scheduled tasks?", default_yes=False):
                _save_tasks([])
                stop_scheduler()
                success("Cleared all scheduled tasks.")
        # Handle Add Task
        elif choices[idx] == "+ Add Task":
            _add_task()
        # Handle task selection
        elif idx < len(tasks):
            _edit_task(idx)


def _add_task():
    """Add a new scheduled task."""
    type_choices = list(TASK_TYPES.values())
    type_choices.append("← Back")
    tidx = pick_option("Task type:", type_choices)
    if tidx >= len(TASK_TYPES):
        return

    task_type = list(TASK_TYPES.keys())[tidx]

    interval_choices = [label for label, _ in INTERVAL_PRESETS]
    interval_choices.append("← Back")
    iidx = pick_option("Run interval:", interval_choices)
    if iidx >= len(INTERVAL_PRESETS):
        return

    interval = INTERVAL_PRESETS[iidx][1]

    tasks = _get_tasks()
    tasks.append({
        "type": task_type,
        "interval": interval,
        "enabled": True,
        "last_run": "",
        "run_count": 0,
    })
    _save_tasks(tasks)
    log_action("Scheduled Task Add", f"{task_type} every {_format_interval(interval)}")
    success(f"Added: {TASK_TYPES[task_type]} every {_format_interval(interval)}")

    # Start scheduler if not running
    start_scheduler()


def _edit_task(idx):
    """Edit or toggle an existing scheduled task."""
    tasks = _get_tasks()
    if idx >= len(tasks):
        return
    task = tasks[idx]

    ttype = TASK_TYPES.get(task.get("type", ""), task.get("type", "?"))
    enabled = task.get("enabled", True)
    toggle_label = "Disable" if enabled else "Enable"

    aidx = pick_option(f"Task: {ttype}", [
        f"{toggle_label} Task",
        "Change Interval",
        "Run Now",
        "Remove Task",
        "← Back",
    ])
    if aidx == 4:
        return
    elif aidx == 0:
        task["enabled"] = not enabled
        _save_tasks(tasks)
        state = "enabled" if task["enabled"] else "disabled"
        info(f"Task {state}: {ttype}")
        if task["enabled"]:
            start_scheduler()
    elif aidx == 1:
        interval_choices = [label for label, _ in INTERVAL_PRESETS]
        interval_choices.append("← Back")
        iidx = pick_option("New interval:", interval_choices)
        if iidx < len(INTERVAL_PRESETS):
            task["interval"] = INTERVAL_PRESETS[iidx][1]
            _save_tasks(tasks)
            success(f"Interval updated: {_format_interval(task['interval'])}")
    elif aidx == 2:
        info(f"Running: {ttype}...")
        _run_task(task)
        _save_tasks(tasks)
        success(f"Completed: {ttype}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif aidx == 3:
        if confirm(f"Remove task: {ttype}?", default_yes=False):
            tasks.pop(idx)
            _save_tasks(tasks)
            log_action("Scheduled Task Remove", ttype)
            success(f"Removed: {ttype}")


def _format_interval(seconds):
    """Format seconds into a human-readable interval string."""
    if seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    else:
        return f"{seconds // 86400}d"
