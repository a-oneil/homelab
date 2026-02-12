"""Docker Volume Management — list, inspect, prune, remove volumes."""

import json

from homelab.modules.transport import ssh_run
from homelab.modules.auditlog import log_action
from homelab.ui import C, pick_option, confirm, success, error, warn


def docker_volumes(host, port=None, label=None):
    """Manage Docker volumes on a host."""
    display = label or host
    while True:
        result = ssh_run(
            "docker volume ls --format '{{.Name}}\\t{{.Driver}}\\t{{.Mountpoint}}' 2>/dev/null",
            host=host, port=port,
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No Docker volumes found.")
            return

        volumes = []
        for line in result.stdout.strip().split("\n"):
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                volumes.append({"name": parts[0], "driver": parts[1], "mount": parts[2]})

        choices = [f"{v['name']:<35} {C.DIM}{v['driver']}{C.RESET}" for v in volumes]
        choices.append("Prune Unused Volumes")
        choices.append("← Back")
        idx = pick_option(f"Docker Volumes ({len(volumes)}):", choices)

        if idx == len(choices) - 1:
            return
        elif idx == len(choices) - 2:
            _volume_prune(host, port=port, label=display)
        elif idx < len(volumes):
            _volume_detail(host, volumes[idx], port=port, label=display)


def _volume_detail(host, vol, port=None, label=None):
    """Show detail for a Docker volume."""
    display = label or host
    result = ssh_run(f"docker volume inspect {vol['name']} 2>/dev/null", host=host, port=port)
    if result.returncode != 0 or not result.stdout.strip():
        error("Could not inspect volume.")
        return

    try:
        data = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError):
        error("Could not parse volume info.")
        return

    name = data.get("Name", "?")
    driver = data.get("Driver", "?")
    mountpoint = data.get("Mountpoint", "?")
    created = data.get("CreatedAt", "?")
    labels = data.get("Labels", {}) or {}

    du_result = ssh_run(f"du -sh {mountpoint} 2>/dev/null", host=host, port=port)
    size = du_result.stdout.strip().split("\t")[0] if du_result.returncode == 0 and du_result.stdout.strip() else "?"

    lines = []
    lines.append(f"\n  {C.BOLD}{name}{C.RESET}\n")
    lines.append(f"  {C.BOLD}Driver:{C.RESET}     {driver}")
    lines.append(f"  {C.BOLD}Mountpoint:{C.RESET} {mountpoint}")
    lines.append(f"  {C.BOLD}Size:{C.RESET}       {size}")
    lines.append(f"  {C.BOLD}Created:{C.RESET}    {created[:19] if len(created) > 19 else created}")
    if labels:
        lines.append(f"  {C.BOLD}Labels:{C.RESET}")
        for k, v in labels.items():
            lines.append(f"    {k}: {C.DIM}{v}{C.RESET}")

    print("\n".join(lines))

    aidx = pick_option(f"Volume: {name}", ["Remove Volume", "← Back"])
    if aidx == 0:
        if confirm(f"Remove volume {name}? Data will be lost.", default_yes=False):
            log_action("Docker Volume Remove", f"{name} on {display}")
            r = ssh_run(f"docker volume rm {name}", host=host, port=port)
            if r.returncode == 0:
                success(f"Removed: {name}")
            else:
                error(f"Failed: {r.stderr.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _volume_prune(host, port=None, label=None):
    """Prune unused Docker volumes on a host."""
    if not confirm("Remove all unused volumes? This cannot be undone.", default_yes=False):
        return
    display = label or host
    log_action("Docker Volume Prune", f"unused volumes on {display}")
    r = ssh_run("docker volume prune -f 2>/dev/null", host=host, port=port)
    if r.returncode == 0:
        success("Pruned unused volumes.")
        if r.stdout.strip():
            print(f"  {C.DIM}{r.stdout.strip()}{C.RESET}")
    else:
        error(f"Failed: {r.stderr.strip()}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
