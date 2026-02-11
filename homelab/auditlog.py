"""Audit Log — track every action taken in homelab with timestamps."""

import json
import os
import time

from homelab.ui import C, pick_option, prompt_text

AUDIT_PATH = os.path.expanduser("~/.homelab_audit.json")
_MAX_ENTRIES = 500


def log_action(action, detail=""):
    """Append an action to the audit log. Called from various modules."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "detail": detail,
    }
    history = _load_log()
    history.append(entry)
    if len(history) > _MAX_ENTRIES:
        history = history[-_MAX_ENTRIES:]
    _save_log(history)


def _load_log():
    if os.path.exists(AUDIT_PATH):
        try:
            with open(AUDIT_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_log(entries):
    with open(AUDIT_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def audit_log_menu():
    """View and search the audit log."""
    while True:
        idx = pick_option("Audit Log:", [
            "View Recent         — last 50 actions",
            "Search Log          — find actions by keyword",
            "Transfer History    — past file transfers",
            "Clear Log           — delete all entries",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 0:
            _view_recent()
        elif idx == 1:
            _search_log()
        elif idx == 2:
            from homelab.files import show_history
            show_history()
        elif idx == 3:
            _clear_log()


def _view_recent(entries=None):
    """Show recent audit log entries."""
    if entries is None:
        entries = _load_log()

    if not entries:
        lines = [f"\n  {C.DIM}No audit log entries yet.{C.RESET}\n"]
        pick_option("", ["← Back"], header="\n".join(lines))
        return

    show = entries[-50:]
    lines = []
    lines.append(f"\n  {C.BOLD}Audit Log{C.RESET}  ({len(entries)} total, showing last {len(show)})\n")
    lines.append(f"  {C.BOLD}{'Timestamp':<20} {'Action':<30} {'Detail'}{C.RESET}")
    lines.append(f"  {'─' * 70}")

    for entry in reversed(show):
        ts = entry.get("timestamp", "?")[:19]
        action = entry.get("action", "?")[:28]
        detail = entry.get("detail", "")[:40]
        lines.append(f"  {C.DIM}{ts}{C.RESET}  {action:<30} {C.DIM}{detail}{C.RESET}")

    lines.append("")
    pick_option("", ["← Back"], header="\n".join(lines))


def _search_log():
    """Search audit log by keyword."""
    query = prompt_text("Search term:")
    if not query:
        return

    entries = _load_log()
    query_lower = query.lower()
    matches = [
        e for e in entries
        if query_lower in e.get("action", "").lower()
        or query_lower in e.get("detail", "").lower()
    ]

    if not matches:
        lines = [f"\n  {C.DIM}No entries matching '{query}'.{C.RESET}\n"]
        pick_option("", ["← Back"], header="\n".join(lines))
        return

    lines = []
    lines.append(f"\n  {C.BOLD}Search Results{C.RESET}  ({len(matches)} matches for '{query}')\n")
    lines.append(f"  {C.BOLD}{'Timestamp':<20} {'Action':<30} {'Detail'}{C.RESET}")
    lines.append(f"  {'─' * 70}")

    for entry in reversed(matches[-50:]):
        ts = entry.get("timestamp", "?")[:19]
        action = entry.get("action", "?")[:28]
        detail = entry.get("detail", "")[:40]
        lines.append(f"  {C.DIM}{ts}{C.RESET}  {action:<30} {C.DIM}{detail}{C.RESET}")

    lines.append("")
    pick_option("", ["← Back"], header="\n".join(lines))


def _clear_log():
    """Clear the entire audit log."""
    from homelab.ui import confirm
    if confirm("Delete all audit log entries?"):
        _save_log([])
        log_action("Audit Log Cleared")
