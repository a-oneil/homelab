"""Systemd Service Manager — list and control systemd units."""

import subprocess

from homelab.modules.transport import ssh_run
from homelab.modules.auditlog import log_action
from homelab.ui import C, pick_option, confirm, success, error, warn


def show_services(host, port=None):
    """Interactive systemd service manager."""
    while True:
        idx = pick_option("Systemd Services:", [
            "Active services   — currently running",
            "Failed services   — units in error state",
            "All services      — every loaded unit",
            "← Back",
        ])
        if idx == 3:
            return
        filters = ["--state=active", "--state=failed", ""]
        _list_services(host, port, filters[idx])


def _list_services(host, port, state_filter):
    """List units, let user pick one for actions."""
    cmd = f"systemctl list-units --type=service --no-pager --plain {state_filter} 2>/dev/null"
    result = ssh_run(cmd, host=host, port=port)
    if result.returncode != 0 or not result.stdout.strip():
        warn("No services found (systemd may not be available).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    lines = result.stdout.strip().split("\n")
    units = []
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        unit = parts[0]
        if not unit.endswith(".service"):
            continue
        active = parts[2]

        if active == "active":
            icon = f"{C.GREEN}●{C.RESET}"
        elif active == "failed":
            icon = f"{C.RED}●{C.RESET}"
        else:
            icon = f"{C.DIM}○{C.RESET}"

        units.append(unit)

    if not units:
        warn("No matching services found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        # Rebuild display each loop for fresh status
        result = ssh_run(cmd, host=host, port=port)
        display = []
        current_units = []
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) < 4 or not parts[0].endswith(".service"):
                    continue
                unit = parts[0]
                active = parts[2]
                sub = parts[3]
                desc = " ".join(parts[4:])[:35]

                if active == "active":
                    icon = f"{C.GREEN}●{C.RESET}"
                elif active == "failed":
                    icon = f"{C.RED}●{C.RESET}"
                else:
                    icon = f"{C.DIM}○{C.RESET}"

                current_units.append(unit)
                display.append(f"{icon} {unit:<35} {sub:<10} {C.DIM}{desc}{C.RESET}")

        if not display:
            warn("No matching services found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        display.append("← Back")
        idx = pick_option(f"Services ({len(current_units)}):", display)
        if idx >= len(current_units):
            return

        _service_actions(host, current_units[idx], port)


def _service_actions(host, unit, port):
    """Actions submenu for a specific systemd unit."""
    while True:
        result = ssh_run(f"systemctl status {unit} --no-pager -l 2>/dev/null | head -15", host=host, port=port)
        if result.returncode is not None and result.stdout:
            print(f"\n  {C.BOLD}{unit}{C.RESET}\n")
            for line in result.stdout.strip().split("\n")[:10]:
                print(f"  {line}")
            print()

        idx = pick_option(f"Service: {unit}", [
            "Start",
            "Stop",
            "Restart",
            "Enable",
            "Disable",
            "View Logs",
            "← Back",
        ])
        if idx == 6:
            return
        elif idx == 5:
            _service_logs(host, unit, port)
        else:
            actions = ["start", "stop", "restart", "enable", "disable"]
            action = actions[idx]
            if action in ("stop", "disable"):
                if not confirm(f"{action.title()} {unit}?", default_yes=False):
                    continue
            r = ssh_run(f"systemctl {action} {unit} 2>&1", host=host, port=port)
            if r.returncode == 0:
                log_action(f"Systemd {action.title()}", f"{unit} on {host}")
                success(f"{action.title()}ed {unit}")
            else:
                error(f"Failed: {r.stdout.strip() or r.stderr.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _service_logs(host, unit, port):
    """Show journalctl output for a unit (TTY)."""
    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.extend([host, f"journalctl -u {unit} -n 100 --no-pager"])
    subprocess.run(cmd)
