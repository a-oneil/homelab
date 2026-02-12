"""Container Update Checker — compare running Docker images against registry."""

import subprocess

from homelab.modules.auditlog import log_action
from homelab.config import CFG
from homelab.ui import C, pick_option, info, error


def _get_docker_hosts():
    """Gather all Docker-capable hosts from config (Unraid + Docker servers)."""
    hosts = []
    unraid_host = CFG.get("unraid_ssh_host", "")
    if unraid_host:
        hosts.append({"name": "Unraid", "host": unraid_host, "port": None})
    for srv in CFG.get("docker_servers", []):
        ssh_host = srv.get("host", "")
        if not ssh_host:
            continue
        user = srv.get("user", "")
        host_arg = f"{user}@{ssh_host}" if user else ssh_host
        port = srv.get("port", "") or None
        hosts.append({"name": srv.get("name", ssh_host), "host": host_arg, "port": port})
    return hosts


def check_all_container_updates():
    """Discover all Docker hosts and let user pick which to check."""
    hosts = _get_docker_hosts()
    if not hosts:
        error("No Docker hosts configured (Unraid or Docker Servers).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    if len(hosts) == 1:
        check_container_updates(host=hosts[0]["host"], port=hosts[0].get("port"),
                                label=hosts[0]["name"])
        return

    choices = [f"{h['name']:<25} {C.DIM}{h['host']}{C.RESET}" for h in hosts]
    choices.append("Check ALL hosts")
    choices.append("← Back")
    idx = pick_option("Check updates on which host?", choices)

    if idx == len(choices) - 1:
        return
    elif idx == len(choices) - 2:
        _check_all_hosts(hosts)
    else:
        check_container_updates(host=hosts[idx]["host"], port=hosts[idx].get("port"),
                                label=hosts[idx]["name"])


def _check_all_hosts(hosts):
    """Check container updates across all Docker hosts at once."""
    all_updates = []
    all_current = []
    all_skipped = []
    host_results = []

    for h in hosts:
        name = h["name"]
        info(f"Checking {name}...")
        updates, current, skipped = _check_host(h["host"], h.get("port"))
        host_results.append((name, updates, current, skipped))
        all_updates.extend(f"{name}/{c}" for c in updates)
        all_current.extend(f"{name}/{c}" for c in current)
        all_skipped.extend(f"{name}/{c}" for c in skipped)

    # Display combined results
    lines = []
    lines.append(f"\n  {C.ACCENT}{C.BOLD}╔══════════════════════════════════════════╗{C.RESET}")
    lines.append(f"  {C.ACCENT}{C.BOLD}║     CONTAINER UPDATE CHECK — ALL HOSTS   ║{C.RESET}")
    lines.append(f"  {C.ACCENT}{C.BOLD}╚══════════════════════════════════════════╝{C.RESET}")
    lines.append("")

    for name, updates, current, skipped in host_results:
        total = len(updates) + len(current) + len(skipped)
        if total == 0:
            lines.append(f"  {C.BOLD}{name}{C.RESET}  {C.DIM}(no containers){C.RESET}")
            continue
        update_s = f"{C.YELLOW}{len(updates)} updates{C.RESET}" if updates else f"{C.GREEN}0 updates{C.RESET}"
        lines.append(f"  {C.BOLD}{name}{C.RESET}  {update_s}  {C.GREEN}{len(current)} current{C.RESET}  "
                     f"{C.DIM}{len(skipped)} skipped{C.RESET}")
        for cname in sorted(updates):
            lines.append(f"    {C.YELLOW}↑{C.RESET} {cname}")

    lines.append("")
    summary = (
        f"  {C.BOLD}Total:{C.RESET} "
        f"{C.YELLOW}{len(all_updates)} updates{C.RESET}  "
        f"{C.GREEN}{len(all_current)} current{C.RESET}  "
        f"{C.DIM}{len(all_skipped)} skipped{C.RESET}"
    )
    lines.append(summary)
    lines.append("")

    header = "\n".join(lines)
    pick_option("", ["← Back"], header=header)


def _check_host(host, port=None):
    """Check a single host, return (updates, current, skipped) lists."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.extend([host, "docker ps --format '{{.Names}}\\t{{.Image}}\\t{{.ID}}' 2>/dev/null"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return [], [], []

    if result.returncode != 0 or not result.stdout.strip():
        return [], [], []

    containers = []
    for line in result.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 3:
            containers.append({"name": parts[0], "image": parts[1], "id": parts[2][:12]})

    if not containers:
        return [], [], []

    image_list = " ".join(f"'{c['image']}'" for c in containers)
    check_script = (
        f"for img in {image_list}; do "
        "local_digest=$(docker image inspect \"$img\" --format '{{.Id}}' 2>/dev/null | cut -c1-19); "
        "remote_digest=$(docker pull \"$img\" 2>/dev/null | grep 'Digest:' | awk '{print $2}' | cut -c1-19); "
        "if [ -z \"$remote_digest\" ]; then "
        "  echo \"${img}\\tSKIP\\t${local_digest}\"; "
        "elif [ \"$local_digest\" != \"$remote_digest\" ]; then "
        "  echo \"${img}\\tUPDATE\\t${local_digest}\\t${remote_digest}\"; "
        "else "
        "  echo \"${img}\\tOK\\t${local_digest}\"; "
        "fi; "
        "done"
    )

    cmd2 = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    if port:
        cmd2.extend(["-p", str(port)])
    cmd2.extend([host, check_script])

    try:
        result = subprocess.run(cmd2, capture_output=True, text=True, timeout=300,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return [], [], []

    updates = []
    current = []
    skipped = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        image = parts[0] if parts else "?"
        status = parts[1] if len(parts) > 1 else "?"
        cname = image
        for c in containers:
            if c["image"] == image:
                cname = c["name"]
                break
        if status == "UPDATE":
            updates.append(cname)
        elif status == "OK":
            current.append(cname)
        else:
            skipped.append(cname)

    return updates, current, skipped


def check_container_updates(host=None, port=None, label=None):
    """Check all running Docker containers for available image updates on a single host."""
    if not host:
        check_all_container_updates()
        return

    display_name = label or host

    while True:
        info(f"Checking container images on {display_name} (this may take a minute)...")
        print()

        log_action("Container Update Check", f"on {display_name}")
        updates, current, skipped = _check_host(host, port)

        if not updates and not current and not skipped:
            error(f"Could not list containers on {display_name}.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        # Display results
        lines = []
        lines.append(f"\n  {C.ACCENT}{C.BOLD}╔══════════════════════════════════╗{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}║     CONTAINER UPDATE CHECK       ║{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}╚══════════════════════════════════╝{C.RESET}")
        lines.append(f"  {C.BOLD}{display_name}{C.RESET}")
        lines.append("")

        if updates:
            lines.append(f"  {C.YELLOW}{C.BOLD}Updates Available ({len(updates)}):{C.RESET}")
            for name in sorted(updates):
                lines.append(f"    {C.YELLOW}↑{C.RESET} {name}")
            lines.append("")

        if current:
            lines.append(f"  {C.GREEN}{C.BOLD}Up to Date ({len(current)}):{C.RESET}")
            for name in sorted(current):
                lines.append(f"    {C.GREEN}✓{C.RESET} {name}")
            lines.append("")

        if skipped:
            lines.append(f"  {C.DIM}Skipped ({len(skipped)}): {', '.join(sorted(skipped))}{C.RESET}")
            lines.append("")

        summary = (
            f"  {C.BOLD}Summary:{C.RESET} "
            f"{C.YELLOW}{len(updates)} updates{C.RESET}  "
            f"{C.GREEN}{len(current)} current{C.RESET}  "
            f"{C.DIM}{len(skipped)} skipped{C.RESET}"
        )
        lines.append(summary)
        lines.append("")

        header = "\n".join(lines)
        idx = pick_option("", ["Refresh", "← Back"], header=header)
        if idx == 1:
            return
