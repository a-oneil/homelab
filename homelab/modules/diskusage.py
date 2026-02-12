"""Disk Usage Analyzer — interactive du browser with drill-down."""

import re

from homelab.modules.transport import ssh_run
from homelab.ui import C, pick_option, bar_chart, info, error


def disk_usage_menu():
    """Top-level entry: pick host, then analyze."""
    from homelab.modules.quickconnect import _gather_hosts
    hosts = _gather_hosts()
    if not hosts:
        error("No SSH hosts configured.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = [
            f"{h.get('name', '?'):<20} {C.DIM}{h.get('host', '?')}{C.RESET}"
            for h in hosts
        ]
        choices.append("← Back")
        idx = pick_option("Analyze disk usage on:", choices)
        if idx >= len(hosts):
            return

        h = hosts[idx]
        analyze_disk_usage(
            host=h["host"],
            port=h.get("port", "") or None,
        )


def analyze_disk_usage(host, path="/", port=None):
    """Interactive disk usage browser with drill-down."""
    current_path = path.rstrip("/") or "/"

    while True:
        info("Scanning directory sizes (this may take a moment)...")
        result = ssh_run(
            f"du -h --max-depth=1 '{current_path}' 2>/dev/null | sort -rh | head -30",
            host=host, port=port,
        )
        if result.returncode != 0 or not result.stdout.strip():
            error(f"Could not analyze {current_path}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        entries = []
        total_size = ""
        for line in result.stdout.strip().split("\n"):
            parts = line.strip().split("\t", 1)
            if len(parts) == 2:
                size, epath = parts[0].strip(), parts[1].strip()
                epath = epath.rstrip("/")
                if epath == current_path or epath == current_path + "/":
                    total_size = size
                else:
                    entries.append((size, epath))

        if not entries:
            info(f"No subdirectories in {current_path}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        # Parse sizes for relative bar display
        size_bytes = []
        for size, _ in entries:
            size_bytes.append(_parse_size(size))
        max_bytes = max(size_bytes) if size_bytes else 1

        choices = []
        for i, (size, epath) in enumerate(entries):
            name = epath.replace(current_path, "").strip("/") or epath
            pct = (size_bytes[i] / max_bytes * 100) if max_bytes > 0 else 0
            bar = bar_chart(int(pct), 100, width=15)
            choices.append(f"{C.ACCENT}{size:>8}{C.RESET}  {bar}  {name}")

        choices.append("───────────────")
        if current_path != path.rstrip("/") and current_path != "/":
            choices.append("↑ Up one level")
        choices.append("← Back")

        header = f"  {C.BOLD}{current_path}{C.RESET}  ({total_size} total)\n"
        idx = pick_option("Disk Usage:", choices, header=header)
        selected = choices[idx]

        if selected == "← Back":
            return
        elif selected.startswith("↑ Up"):
            # Go up one level
            parent = "/".join(current_path.rstrip("/").split("/")[:-1])
            current_path = parent or "/"
        elif selected.startswith("───"):
            continue
        elif idx < len(entries):
            _, selected_path = entries[idx]
            current_path = selected_path


def _parse_size(size_str):
    """Parse human-readable size string to bytes for comparison."""
    m = re.match(r'([\d.]+)\s*([KMGTP]?)', size_str.strip(), re.IGNORECASE)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    multipliers = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
    return int(val * multipliers.get(unit, 1))
