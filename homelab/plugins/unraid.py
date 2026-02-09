"""Unraid service plugin — dashboard, Docker, VMs, compose, logs, scripts."""

import json
import os
import subprocess
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.transport import get_host, ssh_run
from homelab.ui import (
    C, pick_option, confirm, prompt_text, info, success, error, warn,
    bar_chart, check_tool,
)

# ─── Header stats cache ────────────────────────────────────────────────────

_HEADER_CACHE = {
    "timestamp": 0,
    "uptime": "",
    "disk_usage": "",
    "array_status": "",
    "container_count": "",
    "vm_count": "",
}
_CACHE_TTL = 300


def _fetch_header_stats():
    """Fetch server stats in a single SSH call with delimiters."""
    cmd = (
        "uptime -p 2>/dev/null || echo 'unknown';"
        "echo '---SEP---';"
        "df -h /mnt/user 2>/dev/null | tail -1;"
        "echo '---SEP---';"
        "docker ps -q 2>/dev/null | wc -l;"
        "echo '---SEP---';"
        "virsh list --all 2>/dev/null | grep -c 'running\\|shut off' || echo '0'"
    )
    try:
        result = ssh_run(cmd, background=True)
    except Exception:
        return
    if result.returncode != 0:
        return
    parts = result.stdout.split("---SEP---")
    if len(parts) >= 4:
        up_raw = parts[0].strip().replace("up ", "")
        _HEADER_CACHE["uptime"] = up_raw[:25]
        df_line = parts[1].strip()
        df_parts = df_line.split()
        if len(df_parts) >= 5:
            _HEADER_CACHE["disk_usage"] = f"{df_parts[2]}/{df_parts[1]} ({df_parts[4]})"
        containers = parts[2].strip()
        _HEADER_CACHE["container_count"] = containers if containers.isdigit() else "0"
        vms = parts[3].strip()
        _HEADER_CACHE["vm_count"] = vms if vms.isdigit() else "0"
        _HEADER_CACHE["timestamp"] = time.time()


class UnraidPlugin(Plugin):
    name = "Unraid"
    key = "unraid"

    def is_configured(self):
        return bool(CFG.get("unraid_ssh_host"))

    def get_config_fields(self):
        return [
            ("unraid_ssh_host", "Unraid SSH Host", "e.g. root@10.20.0.2", False),
            ("unraid_remote_base", "Unraid Remote Base Path", "e.g. /mnt/user", False),
            ("unraid_trash_path", "Unraid Trash Path", "", False),
            ("unraid_vscode_workspace", "Unraid VSCode Workspace", "", False),
            ("unraid_vscode_ssh_host", "Unraid VSCode SSH Host", "Host from ~/.ssh/config", False),
            ("unraid_api_url", "Unraid API URL", "optional GraphQL endpoint", False),
            ("unraid_api_key", "Unraid API Key", "", True),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_header_stats()
        up = _HEADER_CACHE.get("uptime") or "?"
        disk = _HEADER_CACHE.get("disk_usage") or "?"
        containers = _HEADER_CACHE.get("container_count") or "0"
        vms = _HEADER_CACHE.get("vm_count") or "0"
        return f"Unraid: Up {up} | {disk} | {containers} containers | {vms} VMs"

    def get_menu_items(self):
        return [
            ("Unraid               — files, Docker, VMs, dashboard, parity, scripts", unraid_menu),
        ]

    def get_actions(self):
        from homelab.files import (
            manage_files, fetch_and_transfer, download_from_server,
            upload_local, manage_trash, mount_browser,
        )
        return {
            "Unraid File Manager": ("manage_files", manage_files),
            "Unraid Transfer to Server": ("fetch_and_transfer", fetch_and_transfer),
            "Unraid Upload Local Files": ("upload_local", upload_local),
            "Unraid Download from Server": ("download_from_server", download_from_server),
            "Unraid Dashboard": ("unraid_dashboard", server_dashboard),
            "Unraid Disk Usage": ("unraid_disk_usage", _show_disk_usage),
            "Unraid System Info": ("unraid_system_info", _show_system_info),
            "Unraid SMART Status": ("unraid_smart", _show_smart_status),
            "Unraid Docker": ("unraid_docker", docker_menu),
            "Unraid Docker Containers": ("unraid_containers", docker_containers),
            "Unraid Docker Compose": ("unraid_compose", docker_compose),
            "Unraid Docker Stats": ("unraid_docker_stats", _docker_stats),
            "Unraid Docker Images": ("unraid_docker_images", _docker_images),
            "Unraid Docker Prune": ("unraid_docker_prune", _docker_system_prune),
            "Unraid VMs": ("unraid_vms", vm_management),
            "Unraid Live Logs": ("unraid_logs", live_log_viewer),
            "Unraid User Scripts": ("unraid_scripts", user_scripts),
            "Unraid Open in VSCode": ("open_vscode", open_vscode),
            "Unraid Trash": ("manage_trash", manage_trash),
            "Unraid Mount Browser": ("mount_browser", mount_browser),
            "Unraid Parity Check": ("unraid_parity", _parity_check),
            "Unraid Notifications": ("unraid_notifications", _notification_center),
        }

    def resolve_favorite(self, fav):
        ftype = fav.get("type")
        fid = fav.get("id", "")
        if ftype == "docker_container":
            return lambda name=fid: _container_actions(name)
        elif ftype == "docker_compose":
            projects_path = "/boot/config/plugins/compose.manager/projects"
            return lambda n=fid, pp=projects_path: _manage_compose_project(n, f"{pp}/{n}")
        elif ftype == "unraid_vm":
            return lambda name=fid: _vm_actions(name)


# ─── GraphQL helper ─────────────────────────────────────────────────────────

def _graphql(query, variables=None):
    """Make a GraphQL request to the Unraid API."""
    api_url = CFG.get("unraid_api_url", "")
    api_key = CFG.get("unraid_api_key", "")
    if not api_url or not api_key:
        return None
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    data = json.dumps(payload).encode()
    req = urllib.request.Request(api_url, data=data, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ─── Unraid Sub-Menu ────────────────────────────────────────────────────────

def unraid_menu():
    from homelab.config import local_hostname
    from homelab.files import (
        manage_files, fetch_and_transfer, download_from_server,
        manage_trash, mount_browser,
    )
    hostname = local_hostname()
    while True:
        idx = pick_option("Unraid:", [
            "Unraid File Manager         — browse, search, manage files",
            "Unraid Transfer to Server   — upload local files or download from web",
            f"Unraid Download from Server — pull files to {hostname}",
            "───────────────",
            "Unraid Dashboard            — disk usage, CPU, RAM, SMART, transfers",
            "Unraid Docker               — containers, compose, logs, shell",
            "Unraid VMs                  — start, stop, restart, snapshot",
            "Unraid Parity Check         — status, start, stop, schedule",
            "Unraid Notifications        — view and dismiss system alerts",
            "───────────────",
            "Unraid SSH Shell            — open a terminal session on the server",
            "Unraid Live Logs            — tail syslog or container logs",
            "Unraid User Scripts         — browse and run server scripts",
            "Unraid Open in VSCode       — launch remote workspace",
            "Unraid Trash                — manage deleted files",
            "Unraid Mount Browser        — tree view of shares by size",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 18:
            return
        elif idx in (3, 9, 16):
            continue
        elif idx == 17:
            _add_favorite()
        else:
            funcs = {
                0: manage_files,
                1: fetch_and_transfer,
                2: download_from_server,
                4: server_dashboard,
                5: docker_menu,
                6: vm_management,
                7: _parity_check,
                8: _notification_center,
                10: _ssh_shell,
                11: live_log_viewer,
                12: user_scripts,
                13: open_vscode,
                14: manage_trash,
                15: mount_browser,
            }
            func = funcs.get(idx)
            if func:
                func()


def _ssh_shell():
    """Open an interactive SSH session to the Unraid server."""
    host = get_host()
    info(f"Connecting to {host}...")
    subprocess.run(["ssh", "-t", host])


def _add_favorite():
    """Let user pin an Unraid action to the main menu."""
    from homelab.plugins import add_plugin_favorite
    add_plugin_favorite(UnraidPlugin())


# ─── Dashboard ──────────────────────────────────────────────────────────────

def server_dashboard():
    """Show disk usage, system info, active transfers, and SMART status."""
    while True:
        idx = pick_option("Server Dashboard:", [
            "Disk Usage          — space per share/disk with bar charts",
            "System Info         — CPU, RAM, uptime, temperature",
            "Active Transfers    — running rsync/scp processes",
            "SMART Status        — disk health and diagnostics",
            "───────────────",
            "★ Add to Favorites — pin an action to the main menu",
            "← Back",
        ])

        if idx == 6:
            return
        elif idx == 4:
            continue
        elif idx == 5:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
        elif idx == 0:
            _show_disk_usage()
        elif idx == 1:
            _show_system_info()
        elif idx == 2:
            _show_active_transfers()
        elif idx == 3:
            _show_smart_status()


def _show_disk_usage():
    print(f"\n  {C.BOLD}Disk Usage on {get_host()}{C.RESET}\n")

    result = ssh_run("df -h /mnt/user 2>/dev/null")
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            print(f"  {C.BOLD}Array Overview:{C.RESET}")
            parts = lines[1].split()
            if len(parts) >= 5:
                total = parts[1]
                used = parts[2]
                avail = parts[3]
                pct = parts[4].rstrip("%")
                try:
                    pct_f = int(pct) / 100
                    chart = bar_chart(pct_f, 1.0)
                except ValueError:
                    chart = ""
                print(f"    Total: {total}  Used: {used}  Free: {avail}  {chart}")
            print()

    result = ssh_run("du -sh /mnt/user/*/ 2>/dev/null | sort -rh | head -20")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}Top shares by size:{C.RESET}")
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t", 1)
            if len(parts) == 2:
                size, path = parts
                name = os.path.basename(path.rstrip("/"))
                print(f"    {C.ACCENT}{size:>8}{C.RESET}  {name}/")
        print()

    result = ssh_run("df -h /mnt/disk* /mnt/cache* 2>/dev/null")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}Physical disks:{C.RESET}")
        lines = result.stdout.strip().split("\n")
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 6:
                mount = parts[5]
                total = parts[1]
                used = parts[2]
                pct_str = parts[4].rstrip("%")
                try:
                    pct_f = float(pct_str)
                    chart = bar_chart(pct_f, 100)
                except ValueError:
                    chart = ""
                disk_name = os.path.basename(mount)
                print(f"    {disk_name:<12} {used:>6} / {total:<6}  {chart}")
        print()

    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _show_system_info():
    print(f"\n  {C.BOLD}System Info — {get_host()}{C.RESET}\n")

    result = ssh_run("uptime")
    if result.returncode == 0:
        print(f"  {C.BOLD}Uptime:{C.RESET}  {result.stdout.strip()}")

    result = ssh_run("grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}CPU:{C.RESET}     {result.stdout.strip()}")

    result = ssh_run("nproc")
    if result.returncode == 0:
        print(f"  {C.BOLD}Cores:{C.RESET}   {result.stdout.strip()}")

    result = ssh_run("cat /proc/loadavg")
    if result.returncode == 0:
        parts = result.stdout.strip().split()
        print(f"  {C.BOLD}Load:{C.RESET}    {parts[0]} {parts[1]} {parts[2]}  (1m 5m 15m)")

    result = ssh_run("free -h | grep Mem")
    if result.returncode == 0:
        parts = result.stdout.strip().split()
        if len(parts) >= 3:
            total = parts[1]
            used = parts[2]
            print(f"  {C.BOLD}RAM:{C.RESET}     {used} / {total}")

    result = ssh_run("sensors 2>/dev/null | grep -i 'core 0\\|package\\|cpu' | head -3")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}Temps:{C.RESET}")
        for line in result.stdout.strip().split("\n"):
            print(f"           {line.strip()}")

    result = ssh_run("cat /etc/unraid-version 2>/dev/null")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}Unraid:{C.RESET}  {result.stdout.strip()}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _show_active_transfers():
    print(f"\n  {C.BOLD}Active Transfers on {get_host()}{C.RESET}\n")

    result = ssh_run("ps aux | grep -E 'rsync|scp|rclone' | grep -v grep")
    if result.returncode != 0 or not result.stdout.strip():
        info("No active transfers found.")
    else:
        for line in result.stdout.strip().split("\n"):
            parts = line.split(None, 10)
            if len(parts) >= 11:
                cpu = parts[2]
                mem = parts[3]
                cmd = parts[10]
                print(f"  {C.ACCENT}CPU:{cpu}%  MEM:{mem}%{C.RESET}  {cmd[:80]}")
            else:
                print(f"  {line.strip()[:100]}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _show_smart_status():
    """Show SMART health for all disks or detailed info for one."""
    while True:
        idx = pick_option("SMART Status:", [
            "All disks summary", "Detailed disk info", "← Back",
        ])
        if idx == 2:
            return
        elif idx == 0:
            _smart_summary()
        elif idx == 1:
            _smart_detail()


def _smart_summary():
    print(f"\n  {C.BOLD}SMART Health Summary — {get_host()}{C.RESET}\n")
    result = ssh_run("ls /dev/sd? 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        warn("No SMART-capable disks found.")
        input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
        return
    disks = result.stdout.strip().split()
    for disk in disks:
        disk_name = os.path.basename(disk)
        hr = ssh_run(f"smartctl -H {disk} 2>/dev/null | grep 'SMART overall-health'")
        if hr.returncode == 0 and "PASSED" in hr.stdout:
            status = f"{C.GREEN}PASSED{C.RESET}"
        elif hr.returncode == 0:
            status = f"{C.RED}FAILED{C.RESET}"
        else:
            status = f"{C.DIM}N/A{C.RESET}"
        tr = ssh_run(f"smartctl -A {disk} 2>/dev/null | grep -i temperature | head -1")
        temp = "?"
        if tr.returncode == 0 and tr.stdout.strip():
            parts = tr.stdout.strip().split()
            if len(parts) >= 10:
                temp = parts[9] + "C"
        print(f"    {disk_name}  {status}  Temp: {temp}")
    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _smart_detail():
    result = ssh_run("lsblk -d -o NAME,SIZE,MODEL 2>/dev/null | grep '^sd'")
    if result.returncode != 0 or not result.stdout.strip():
        error("No disks found.")
        return
    lines = result.stdout.strip().split("\n")
    disks = []
    choices = []
    for line in lines:
        parts = line.split(None, 2)
        if len(parts) >= 2:
            name = parts[0]
            size = parts[1]
            model = parts[2] if len(parts) > 2 else "Unknown"
            disks.append(name)
            choices.append(f"{name} ({size}) - {model}")
    choices.append("← Back")
    idx = pick_option("Select disk:", choices)
    if idx >= len(disks):
        return
    disk = disks[idx]
    print(f"\n  {C.BOLD}SMART Info for /dev/{disk}{C.RESET}\n")
    hr = ssh_run(f"smartctl -H /dev/{disk} 2>/dev/null")
    if hr.returncode == 0:
        for line in hr.stdout.strip().split("\n"):
            if "overall-health" in line.lower():
                print(f"  {C.BOLD}Health:{C.RESET} {line.strip()}")
        print()
    ar = ssh_run(f"smartctl -A /dev/{disk} 2>/dev/null")
    if ar.returncode == 0:
        print(f"  {C.BOLD}Key Attributes:{C.RESET}")
        keywords = ["Power_On_Hours", "Temperature", "Reallocated", "Pending", "Current_Pending"]
        for line in ar.stdout.strip().split("\n"):
            if any(kw in line for kw in keywords):
                print(f"    {line.strip()}")
        print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Docker (merged with Compose) ──────────────────────────────────────────

def docker_menu():
    while True:
        idx = pick_option("Docker:", [
            "Containers         — start, stop, restart, shell, logs",
            "Compose Projects   — deploy, pull, edit compose files",
            "Docker Stats       — CPU, RAM, network per container",
            "Image Management   — list images, prune unused",
            "System Prune       — clean unused images, volumes, networks",
            "Bulk Operations    — stop all, restart all, start all",
            "───────────────",
            "★ Add to Favorites — pin an action to the main menu",
            "← Back",
        ])
        if idx == 8:
            return
        elif idx == 6:
            continue
        elif idx == 7:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
        elif idx == 0:
            docker_containers()
        elif idx == 1:
            docker_compose()
        elif idx == 2:
            _docker_stats()
        elif idx == 3:
            _docker_images()
        elif idx == 4:
            _docker_system_prune()
        elif idx == 5:
            _docker_bulk_ops()


def _docker_stats():
    """Show CPU, RAM, and network usage per container."""
    result = ssh_run(
        "docker stats --no-stream --format "
        "'{{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}\\t{{.MemPerc}}\\t{{.NetIO}}' 2>/dev/null"
    )
    if result.returncode != 0 or not result.stdout.strip():
        error("Failed to get Docker stats.")
        return

    print(f"\n  {C.BOLD}Docker Resource Usage{C.RESET}\n")
    print(f"  {C.DIM}{'Container':<28} {'CPU':>7}  {'Memory':>22}  {'Net I/O':>20}{C.RESET}")
    print(f"  {'─' * 82}")

    lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    for line in sorted(lines):
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        name, cpu, mem, mem_pct, net = parts
        cpu_val = cpu.rstrip("%")
        try:
            cpu_f = float(cpu_val)
            if cpu_f > 50:
                cpu_color = C.RED
            elif cpu_f > 10:
                cpu_color = C.YELLOW
            else:
                cpu_color = C.GREEN
        except ValueError:
            cpu_color = C.RESET
        print(f"  {name:<28} {cpu_color}{cpu:>7}{C.RESET}  {mem:>22}  {net:>20}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_images():
    """List Docker images with option to prune."""
    while True:
        result = ssh_run(
            "docker images --format '{{.Repository}}:{{.Tag}}\\t{{.Size}}\\t{{.ID}}\\t{{.CreatedSince}}' 2>/dev/null"
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No Docker images found.")
            return

        lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        images = []
        choices = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            repo_tag, size, img_id, created = parts
            images.append({"repo": repo_tag, "size": size, "id": img_id})
            choices.append(f"{repo_tag:<50} {size:>10}  {created}")

        # Check for dangling images
        dangling = ssh_run("docker images -f dangling=true -q 2>/dev/null")
        dangling_count = len([line for line in dangling.stdout.strip().split("\n") if line.strip()]) if dangling.returncode == 0 and dangling.stdout.strip() else 0

        choices.append("───────────────")
        if dangling_count > 0:
            choices.append(f"Prune dangling images ({dangling_count} unused)")
        choices.append("Prune all unused images")
        choices.append("← Back")

        hdr = f"\n  {C.BOLD}Docker Images{C.RESET} ({len(images)} total)\n"
        idx = pick_option("", choices, header=hdr)
        choice = choices[idx]

        if choice == "← Back":
            return
        elif "────" in choice:
            continue
        elif "Prune dangling" in choice:
            if confirm(f"Remove {dangling_count} dangling image(s)?"):
                info("Pruning dangling images...")
                ssh_run("docker image prune -f", capture=False)
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Prune all" in choice:
            if confirm("Remove ALL unused images? This cannot be undone.", default_yes=False):
                info("Pruning all unused images...")
                ssh_run("docker image prune -a -f", capture=False)
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx < len(images):
            img = images[idx]
            action = pick_option(
                f"Image: {img['repo']} ({img['size']})",
                ["Remove image", "Cancel"],
            )
            if action == 0:
                if confirm(f"Remove {img['repo']}?", default_yes=False):
                    r = ssh_run(f"docker rmi {img['id']}")
                    if r.returncode == 0:
                        success(f"Removed: {img['repo']}")
                    else:
                        error(f"Failed (image may be in use): {r.stderr.strip()}")


def _docker_system_prune():
    """Clean up unused Docker resources."""
    result = ssh_run("docker system df 2>/dev/null")
    if result.returncode == 0 and result.stdout.strip():
        print(f"\n  {C.BOLD}Docker Disk Usage{C.RESET}\n")
        for line in result.stdout.strip().split("\n"):
            print(f"  {line}")
        print()

    idx = pick_option("System Prune:", [
        "Basic prune     — dangling images, stopped containers, unused networks",
        "Full prune      — ALL unused images, volumes, networks (aggressive)",
        "← Back",
    ])
    if idx == 2:
        return
    elif idx == 0:
        if confirm("Run basic Docker system prune?"):
            info("Pruning...")
            ssh_run("docker system prune -f", capture=False)
            print()
            success("Basic prune complete.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif idx == 1:
        if confirm("Run FULL prune? This removes ALL unused images and volumes.", default_yes=False):
            info("Pruning everything...")
            ssh_run("docker system prune -a --volumes -f", capture=False)
            print()
            success("Full prune complete.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_bulk_ops():
    """Bulk operations on Docker containers."""
    result = ssh_run(
        "docker ps -a --format '{{.Names}}\\t{{.Status}}' 2>/dev/null"
    )
    if result.returncode != 0 or not result.stdout.strip():
        warn("No containers found.")
        return

    lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    running = []
    stopped = []
    for line in lines:
        parts = line.split("\t")
        name = parts[0]
        status = parts[1] if len(parts) > 1 else ""
        if "Up" in status:
            running.append(name)
        else:
            stopped.append(name)

    print(f"\n  {C.BOLD}Containers:{C.RESET} {len(running)} running, {len(stopped)} stopped\n")

    idx = pick_option("Bulk Operation:", [
        f"Stop all running ({len(running)})",
        f"Restart all running ({len(running)})",
        f"Start all stopped ({len(stopped)})",
        "← Back",
    ])
    if idx == 3:
        return
    elif idx == 0 and running:
        if confirm(f"Stop {len(running)} running container(s)?"):
            for name in running:
                info(f"Stopping {name}...")
                ssh_run(f"docker stop {name}")
            success(f"Stopped {len(running)} containers.")
    elif idx == 1 and running:
        if confirm(f"Restart {len(running)} running container(s)?"):
            for name in running:
                info(f"Restarting {name}...")
                ssh_run(f"docker restart {name}")
            success(f"Restarted {len(running)} containers.")
    elif idx == 2 and stopped:
        if confirm(f"Start {len(stopped)} stopped container(s)?"):
            for name in stopped:
                info(f"Starting {name}...")
                ssh_run(f"docker start {name}")
            success(f"Started {len(stopped)} containers.")
    else:
        info("No containers to operate on.")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _container_actions(name):
    """Show actions for a single Docker container (used by list and favorites)."""
    result = ssh_run(
        f"docker ps -a --filter name=^{name}$ --format '{{{{.Status}}}}' 2>/dev/null"
    )
    status = result.stdout.strip() if result.returncode == 0 else "unknown"
    is_running = "Up" in status

    if is_running:
        action_choices = [
            "Stop", "Restart", "Logs (last 50 lines)", "Live logs (tail -f)",
            "Shell", "Inspect", "Update (pull + restart)",
            "Open appdata", "★ Favorite", "← Back",
        ]
    else:
        action_choices = [
            "Start", "Logs (last 50 lines)", "Inspect",
            "Remove", "★ Favorite", "← Back",
        ]

    action = pick_option(f"Container: {name} — {status}", action_choices)
    action_label = action_choices[action]

    if action_label == "← Back":
        return
    elif action_label == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("docker_container", name, f"Docker: {name}")
    elif action_label == "Stop":
        info(f"Stopping {name}...")
        ssh_run(f"docker stop {name}", capture=False)
    elif action_label == "Start":
        info(f"Starting {name}...")
        ssh_run(f"docker start {name}", capture=False)
    elif action_label == "Restart":
        info(f"Restarting {name}...")
        ssh_run(f"docker restart {name}", capture=False)
    elif action_label == "Logs (last 50 lines)":
        print(f"\n  {C.BOLD}Last 50 lines of {name}:{C.RESET}\n")
        ssh_run(f"docker logs --tail 50 {name}", capture=False)
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif action_label == "Live logs (tail -f)":
        info(f"Tailing logs for {name} (Ctrl+C to exit)...")
        try:
            subprocess.run(["ssh", "-t", get_host(), f"docker logs -f --tail 100 {name}"])
        except KeyboardInterrupt:
            print()
    elif action_label == "Shell":
        info(f"Opening shell in {name} (type 'exit' to quit)...")
        r = subprocess.run(
            ["ssh", "-t", get_host(), f"docker exec -it {name} /bin/bash"]
        )
        if r.returncode != 0:
            info("Bash not available, trying sh...")
            subprocess.run(
                ["ssh", "-t", get_host(), f"docker exec -it {name} /bin/sh"]
            )
    elif action_label == "Inspect":
        _container_inspect(name)
    elif action_label == "Update (pull + restart)":
        _container_update(name)
    elif action_label == "Remove":
        _container_remove(name)
    elif action_label == "Open appdata":
        appdata_path = f"/mnt/user/appdata/{name}"
        check = ssh_run(f"test -d '{appdata_path}' && echo 'exists'")
        if check.returncode == 0 and "exists" in check.stdout:
            from homelab.files import manage_files_at
            manage_files_at(appdata_path)
        else:
            warn(f"Appdata folder not found: {appdata_path}")


def _container_inspect(name):
    """Show detailed info for a container."""
    result = ssh_run(
        f"docker inspect {name} --format '"
        "Image: {{{{.Config.Image}}}}\\n"
        "Created: {{{{.Created}}}}\\n"
        "RestartPolicy: {{{{.HostConfig.RestartPolicy.Name}}}}\\n"
        "Health: {{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{else}}}}N/A{{{{end}}}}\\n"
        "' 2>/dev/null"
    )
    print(f"\n  {C.BOLD}Container: {name}{C.RESET}\n")
    if result.returncode == 0:
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                print(f"  {line.strip()}")

    # Ports
    ports_r = ssh_run(
        f"docker inspect {name} --format '{{{{range $p, $conf := .NetworkSettings.Ports}}}}"
        f"{{{{$p}}}} -> {{{{range $conf}}}}{{{{.HostPort}}}}{{{{end}}}}\\n{{{{end}}}}' 2>/dev/null"
    )
    if ports_r.returncode == 0 and ports_r.stdout.strip():
        print(f"\n  {C.BOLD}Ports:{C.RESET}")
        for line in ports_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Volumes/Mounts
    mounts_r = ssh_run(
        f"docker inspect {name} --format '{{{{range .Mounts}}}}"
        f"{{{{.Source}}}} -> {{{{.Destination}}}} ({{{{.Type}}}})\\n{{{{end}}}}' 2>/dev/null"
    )
    if mounts_r.returncode == 0 and mounts_r.stdout.strip():
        print(f"\n  {C.BOLD}Mounts:{C.RESET}")
        for line in mounts_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Networks
    nets_r = ssh_run(
        f"docker inspect {name} --format '{{{{range $k, $v := .NetworkSettings.Networks}}}}"
        f"{{{{$k}}}} ({{{{$v.IPAddress}}}})\\n{{{{end}}}}' 2>/dev/null"
    )
    if nets_r.returncode == 0 and nets_r.stdout.strip():
        print(f"\n  {C.BOLD}Networks:{C.RESET}")
        for line in nets_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Environment variables (filtered for secrets)
    env_r = ssh_run(
        f"docker inspect {name} --format '{{{{range .Config.Env}}}}{{{{.}}}}\\n{{{{end}}}}' 2>/dev/null"
    )
    if env_r.returncode == 0 and env_r.stdout.strip():
        print(f"\n  {C.BOLD}Environment:{C.RESET}")
        for line in env_r.stdout.strip().split("\n"):
            if line.strip():
                key = line.split("=")[0] if "=" in line else line
                lower_key = key.lower()
                if any(s in lower_key for s in ("pass", "secret", "token", "key", "api")):
                    print(f"    {key}={C.DIM}****{C.RESET}")
                else:
                    print(f"    {line.strip()}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _container_update(name):
    """Pull latest image and restart a container."""
    # Get current image
    img_r = ssh_run(
        f"docker inspect {name} --format '{{{{.Config.Image}}}}' 2>/dev/null"
    )
    if img_r.returncode != 0 or not img_r.stdout.strip():
        error("Could not determine container image.")
        return

    image = img_r.stdout.strip()
    print(f"\n  {C.BOLD}Update: {name}{C.RESET}")
    print(f"  {C.BOLD}Image:{C.RESET}  {image}\n")

    if not confirm(f"Pull latest {image} and restart {name}?"):
        return

    info(f"Pulling {image}...")
    subprocess.run(["ssh", "-t", get_host(), f"docker pull {image}"])

    info(f"Restarting {name}...")
    ssh_run(f"docker stop {name}")
    ssh_run(f"docker start {name}")
    success(f"Updated and restarted: {name}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _container_remove(name):
    """Remove a stopped container."""
    if not confirm(f"Remove container {name}?", default_yes=False):
        return
    r = ssh_run(f"docker rm {name}")
    if r.returncode == 0:
        success(f"Removed: {name}")
    else:
        error(f"Failed: {r.stderr.strip()}")


def docker_containers():
    while True:
        result = ssh_run(
            "docker ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}\\t{{.Ports}}' 2>/dev/null"
        )
        if result.returncode != 0:
            error("Failed to list containers. Is Docker running?")
            return

        lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        if not lines:
            warn("No containers found.")
            return

        containers = []
        choices = []
        for line in lines:
            parts = line.split("\t")
            name = parts[0] if len(parts) > 0 else "?"
            status = parts[1] if len(parts) > 1 else "?"
            image = parts[2] if len(parts) > 2 else "?"
            containers.append({"name": name, "status": status, "image": image})
            choices.append(f"{name}  [{status}]  ({image})")

        choices.append("───────────────")
        choices.append("★ Add to Favorites")
        choices.append("← Back")

        print()
        idx = pick_option("Select a container:", choices)

        if idx == len(containers) + 2:
            return
        elif idx == len(containers) + 1:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
            continue
        elif idx == len(containers):
            continue  # separator

        _container_actions(containers[idx]["name"])


def docker_compose():
    """Browse and manage docker compose projects."""
    projects_path = "/boot/config/plugins/compose.manager/projects"
    while True:
        result = ssh_run(
            f"find '{projects_path}' -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort"
        )
        project_paths = []
        project_names = []
        if result.returncode == 0 and result.stdout.strip():
            project_paths = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
            project_names = [os.path.basename(p) for p in project_paths]
        choices = [f"{n}/" for n in project_names]
        choices.append("+ Create new project")
        choices.append("───────────────")
        choices.append("★ Add to Favorites")
        choices.append("← Back")
        idx = pick_option("Docker Compose projects:", choices)
        if idx == len(project_names) + 3:
            return
        elif idx == len(project_names) + 2:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
            continue
        elif idx == len(project_names) + 1:
            continue  # separator
        elif idx == len(project_names):
            _create_compose_project(projects_path)
        else:
            _manage_compose_project(project_names[idx], project_paths[idx])


def _manage_compose_project(name, path):
    """Manage a single compose project."""
    compose_file = f"{path}/docker-compose.yml"
    check = ssh_run(f"test -f '{compose_file}' && echo 'exists'")
    if check.returncode != 0 or "exists" not in check.stdout:
        compose_file = f"{path}/compose.yaml"
        check = ssh_run(f"test -f '{compose_file}' && echo 'exists'")
        if check.returncode != 0 or "exists" not in check.stdout:
            error(f"No docker-compose.yml found in {name}")
            return
    env_file = f"{path}/.env"
    while True:
        # Fetch service status for header
        status_r = ssh_run(
            f"cd '{path}' && docker-compose ps --format '{{{{.Name}}}}\\t{{{{.Status}}}}' 2>/dev/null"
        )
        svc_lines = []
        if status_r.returncode == 0 and status_r.stdout.strip():
            svc_lines = [line.strip() for line in status_r.stdout.strip().split("\n") if line.strip()]

        hdr = f"\n  {C.BOLD}Project: {name}{C.RESET}\n"
        if svc_lines:
            for sl in svc_lines:
                parts = sl.split("\t")
                svc_name = parts[0] if parts else "?"
                svc_status = parts[1] if len(parts) > 1 else "?"
                if "unhealthy" in svc_status.lower():
                    icon = f"{C.RED}●{C.RESET}"
                elif "Up" in svc_status:
                    icon = f"{C.GREEN}●{C.RESET}"
                else:
                    icon = f"{C.DIM}○{C.RESET}"
                print(f"  {icon} {svc_name}  {C.DIM}{svc_status}{C.RESET}")
            hdr += "\n"

        menu = [
            "Up (deploy)", "Down (stop)", "Pull & Up (update)",
            "Logs (last 50 lines)", "Restart service    — restart a single service",
            "───────────────",
            "Edit compose file", "Edit .env file",
            "Validate           — check compose syntax",
            "Delete project     — remove project directory",
            "───────────────",
            "★ Favorite", "← Back",
        ]
        idx = pick_option("", menu, header=hdr)
        choice = menu[idx]

        if choice == "← Back":
            return
        elif "────" in choice:
            continue
        elif choice == "★ Favorite":
            from homelab.plugins import add_item_favorite
            add_item_favorite("docker_compose", name, f"Compose: {name}")
        elif choice == "Edit compose file":
            subprocess.run(["ssh", "-t", get_host(), f"nano '{compose_file}'"])
        elif choice == "Edit .env file":
            ssh_run(f"touch '{env_file}'")
            subprocess.run(["ssh", "-t", get_host(), f"nano '{env_file}'"])
        elif "Validate" in choice:
            info("Validating compose file...")
            r = ssh_run(f"cd '{path}' && docker-compose config 2>&1")
            if r.returncode == 0:
                success("Compose file is valid.")
            else:
                error("Validation failed:")
                print(f"\n{r.stdout.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Delete project" in choice:
            if confirm(f"Delete project '{name}' and all its files?", default_yes=False):
                r = ssh_run(f"cd '{path}' && docker-compose down 2>/dev/null; rm -rf '{path}'")
                if r.returncode == 0:
                    success(f"Deleted project: {name}")
                else:
                    error(f"Failed: {r.stderr.strip()}")
                return
        elif "Restart service" in choice:
            _compose_restart_service(path, name)
        elif choice == "Up (deploy)":
            info("Deploying...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose up -d"])
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Down (stop)":
            info("Stopping...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose down"])
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Pull & Up (update)":
            info("Pulling and deploying...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose pull && docker-compose up -d"])
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Logs (last 50 lines)":
            info("Fetching logs...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose logs --tail 50"])
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _compose_restart_service(path, project_name):
    """Restart a single service within a compose project."""
    result = ssh_run(
        f"cd '{path}' && docker-compose config --services 2>/dev/null"
    )
    if result.returncode != 0 or not result.stdout.strip():
        error("Could not list services.")
        return

    services = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
    if not services:
        warn("No services found.")
        return

    choices = services + ["← Back"]
    idx = pick_option(f"Restart which service in {project_name}?", choices)
    if idx >= len(services):
        return

    svc = services[idx]
    info(f"Restarting {svc}...")
    subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose restart {svc}"])
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _create_compose_project(projects_path):
    """Create a new docker compose project directory with a compose file."""
    name = prompt_text("New project name:")
    if not name:
        warn("Cancelled.")
        return
    # Sanitize name
    name = name.strip().replace(" ", "-").lower()
    project_dir = f"{projects_path}/{name}"
    check = ssh_run(f"test -d '{project_dir}' && echo 'exists'")
    if check.returncode == 0 and "exists" in check.stdout:
        error(f"Project '{name}' already exists.")
        return
    ssh_run(f"mkdir -p '{project_dir}'")
    compose_file = f"{project_dir}/docker-compose.yml"
    ssh_run(f"echo 'version: \"3\"\\nservices:\\n' > '{compose_file}'")
    success(f"Created project: {name}")
    info("Opening compose file for editing...")
    subprocess.run(["ssh", "-t", get_host(), f"nano '{compose_file}'"])


# ─── VM Management ─────────────────────────────────────────────────────────

def _vm_actions(name):
    """Show actions for a single VM (used by list and favorites)."""
    result = ssh_run(f"virsh domstate {name} 2>/dev/null")
    state = result.stdout.strip() if result.returncode == 0 else "unknown"
    is_running = "running" in state

    if is_running:
        action_choices = ["Shutdown", "Force off", "Restart", "Snapshot", "★ Favorite", "← Back"]
    else:
        action_choices = ["Start", "Snapshot", "★ Favorite", "← Back"]

    action = pick_option(f"VM: {name} — {state}", action_choices)
    action_label = action_choices[action]

    if action_label == "← Back":
        return
    elif action_label == "★ Favorite":
        from homelab.plugins import add_item_favorite
        add_item_favorite("unraid_vm", name, f"VM: {name}")
    elif action_label == "Start":
        info(f"Starting {name}...")
        ssh_run(f"virsh start {name}", capture=False)
    elif action_label == "Shutdown":
        info(f"Shutting down {name}...")
        ssh_run(f"virsh shutdown {name}", capture=False)
    elif action_label == "Force off":
        if confirm(f"Force off {name}?", default_yes=False):
            ssh_run(f"virsh destroy {name}", capture=False)
    elif action_label == "Restart":
        info(f"Restarting {name}...")
        ssh_run(f"virsh shutdown {name}", capture=False)
        info("Waiting for shutdown...")
        time.sleep(5)
        ssh_run(f"virsh start {name}", capture=False)
    elif action_label == "Snapshot":
        snap_name = prompt_text("Snapshot name:") or f"snap_{int(time.time())}"
        info(f"Creating snapshot '{snap_name}'...")
        r = ssh_run(f"virsh snapshot-create-as {name} {snap_name}")
        if r.returncode == 0:
            success(f"Snapshot created: {snap_name}")
        else:
            error(f"Failed: {r.stderr.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def vm_management():
    """Manage VMs via virsh (SSH) or GraphQL if available."""
    while True:
        # Try virsh
        result = ssh_run("virsh list --all 2>/dev/null")
        if result.returncode != 0 or not result.stdout.strip():
            warn("No VMs found or libvirt not available.")
            return

        lines = result.stdout.strip().split("\n")
        vms = []
        choices = []
        for line in lines[2:]:  # Skip header
            parts = line.strip().split(None, 2)
            if len(parts) >= 3:
                name, state = parts[1], parts[2]
                vms.append({"name": name, "state": state})
                choices.append(f"{name}  [{state}]")
            elif len(parts) == 2:
                name, state = parts[0], parts[1]
                vms.append({"name": name, "state": state})
                choices.append(f"{name}  [{state}]")

        if not vms:
            warn("No VMs found.")
            return

        choices.append("← Back")
        idx = pick_option("Virtual Machines:", choices)
        if idx >= len(vms):
            return

        _vm_actions(vms[idx]["name"])


# ─── Live Logs ──────────────────────────────────────────────────────────────

def live_log_viewer():
    """View live system or container logs."""
    while True:
        idx = pick_option("Live Logs:", [
            "System log (syslog)",
            "Docker container log",
            "───────────────",
            "★ Add to Favorites",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 2:
            continue
        elif idx == 3:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
        elif idx == 0:
            info("Viewing system log (Ctrl+C to exit)...")
            try:
                subprocess.run(["ssh", "-t", get_host(), "tail -f /var/log/syslog"])
            except KeyboardInterrupt:
                print()
        elif idx == 1:
            _live_docker_log()


def _live_docker_log():
    result = ssh_run("docker ps --format '{{.Names}}\\t{{.Status}}' 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        error("No running containers found.")
        return
    lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    containers = []
    choices = []
    for line in lines:
        parts = line.split("\t")
        name = parts[0] if parts else "?"
        status = parts[1] if len(parts) > 1 else "?"
        containers.append(name)
        choices.append(f"{name} [{status}]")
    choices.append("← Back")
    idx = pick_option("Select container:", choices)
    if idx >= len(containers):
        return
    container = containers[idx]
    info(f"Viewing logs for {container} (Ctrl+C to exit)...")
    try:
        subprocess.run(["ssh", "-t", get_host(), f"docker logs -f --tail 100 {container}"])
    except KeyboardInterrupt:
        print()


# ─── User Scripts ───────────────────────────────────────────────────────────

def user_scripts():
    """Browse and run Unraid user scripts."""
    scripts_path = "/boot/config/plugins/user.scripts/scripts"
    while True:
        result = ssh_run(
            f"find '{scripts_path}' -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort"
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No user scripts found.")
            return
        script_paths = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        script_names = [os.path.basename(p) for p in script_paths]
        choices = script_names + ["───────────────", "★ Add to Favorites", "← Back"]
        idx = pick_option("User Scripts:", choices)
        if idx == len(script_names) + 2:
            return
        elif idx == len(script_names) + 1:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
            continue
        elif idx == len(script_names):
            continue  # separator
        if idx >= len(script_names):
            continue
        _manage_user_script(script_names[idx], script_paths[idx])


def _manage_user_script(name, path):
    script_file = f"{path}/script"
    check = ssh_run(f"test -f '{script_file}' && echo 'exists'")
    if check.returncode != 0 or "exists" not in check.stdout:
        error(f"No script file found for {name}")
        return
    desc_r = ssh_run(f"cat '{path}/description' 2>/dev/null")
    description = desc_r.stdout.strip() if desc_r.returncode == 0 else ""
    hdr = f"\n  {C.BOLD}Script: {name}{C.RESET}\n"
    if description:
        hdr += f"  {C.DIM}{description[:80]}{C.RESET}\n"
    while True:
        idx = pick_option("", ["Run script", "View source", "← Back"], header=hdr)
        if idx == 2:
            return
        elif idx == 0:
            info(f"Running {name}...")
            print()
            subprocess.run(["ssh", "-t", get_host(), f"bash '{script_file}'"])
            print()
            input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx == 1:
            print(f"\n  {C.BOLD}Source: {name}{C.RESET}\n")
            r = ssh_run(f"cat '{script_file}'")
            if r.returncode == 0:
                print(r.stdout)
            else:
                error("Could not read script file.")
            print()
            input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Open in VSCode ────────────────────────────────────────────────────────

def open_vscode():
    """Open VSCode with Remote-SSH to the server."""
    workspace = CFG.get("unraid_vscode_workspace", "")
    if not workspace:
        error("No workspace configured. Set VSCode Workspace in Settings.")
        return

    ssh_alias = CFG.get("unraid_vscode_ssh_host", "")
    if not ssh_alias:
        error("No SSH host alias configured. Set VSCode SSH Host in Settings.")
        info("This should match a Host entry in ~/.ssh/config (e.g. 'unraid').")
        return

    if not check_tool("code"):
        error("'code' command not found. Install VSCode CLI.")
        return

    info(f"Opening VSCode → {ssh_alias}:{workspace}")
    subprocess.Popen(
        ["code", "--remote", f"ssh-remote+{ssh_alias}", workspace],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    success("VSCode launched.")


# ─── Parity Check ──────────────────────────────────────────────────────────

def _parity_check():
    """View parity check status, start/stop, and schedule."""
    while True:
        # Get parity status from /proc/mdcmd or mdstat
        result = ssh_run("cat /proc/mdstat 2>/dev/null")
        mdstat = result.stdout.strip() if result.returncode == 0 else ""

        # Check if parity is running
        parity_running = False
        progress = ""
        if "recovery" in mdstat or "check" in mdstat:
            parity_running = True
            for line in mdstat.split("\n"):
                if "%" in line:
                    progress = line.strip()

        # Get last parity check info from syslog
        last_check = ssh_run(
            "grep -i 'parity.*check\\|parity.*sync' /var/log/syslog 2>/dev/null | tail -3"
        )

        print(f"\n  {C.BOLD}Parity Check Status{C.RESET}\n")
        if parity_running:
            print(f"  Status: {C.YELLOW}Running{C.RESET}")
            if progress:
                print(f"  Progress: {progress}")
        else:
            print(f"  Status: {C.GREEN}Idle{C.RESET}")

        if last_check.returncode == 0 and last_check.stdout.strip():
            print(f"\n  {C.BOLD}Recent parity log:{C.RESET}")
            for line in last_check.stdout.strip().split("\n"):
                print(f"    {C.DIM}{line.strip()}{C.RESET}")

        # Show array status
        if mdstat:
            print(f"\n  {C.BOLD}Array Status:{C.RESET}")
            for line in mdstat.split("\n")[:6]:
                if line.strip():
                    print(f"    {line.strip()}")

        if parity_running:
            choices = ["Cancel parity check", "Refresh", "← Back"]
        else:
            choices = ["Start parity check", "Start correcting check", "Refresh", "← Back"]

        idx = pick_option("Parity:", choices)
        choice = choices[idx]

        if choice == "← Back":
            return
        elif choice == "Refresh":
            continue
        elif choice == "Start parity check":
            if confirm("Start a non-correcting parity check?"):
                r = ssh_run("mdcmd check noCorrect 2>/dev/null || echo 'mdcmd not available'")
                if r.returncode == 0:
                    success("Parity check started (non-correcting).")
                else:
                    error(f"Failed: {r.stderr.strip()}")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Start correcting check":
            if confirm("Start a CORRECTING parity check?", default_yes=False):
                r = ssh_run("mdcmd check 2>/dev/null || echo 'mdcmd not available'")
                if r.returncode == 0:
                    success("Parity check started (correcting).")
                else:
                    error(f"Failed: {r.stderr.strip()}")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Cancel parity check":
            if confirm("Cancel the running parity check?", default_yes=False):
                r = ssh_run("mdcmd nocheck 2>/dev/null")
                if r.returncode == 0:
                    success("Parity check cancelled.")
                else:
                    error(f"Failed: {r.stderr.strip()}")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Notification Center ───────────────────────────────────────────────────

def _notification_center():
    """View and dismiss Unraid system notifications."""
    while True:
        # Notifications are stored in /tmp/notifications/
        result = ssh_run(
            "find /tmp/notifications -name '*.notify' -type f 2>/dev/null | sort -r | head -50"
        )
        if result.returncode != 0 or not result.stdout.strip():
            info("No notifications found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        notifications = []

        for nf in files:
            content = ssh_run(f"cat '{nf}' 2>/dev/null")
            if content.returncode != 0:
                continue
            # Parse notification fields
            notif = {"file": nf}
            for line in content.stdout.strip().split("\n"):
                if "=" in line:
                    key, _, val = line.partition("=")
                    notif[key.strip()] = val.strip()
            if notif.get("event") or notif.get("subject"):
                notifications.append(notif)

        if not notifications:
            info("No notifications found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        choices = []
        for n in notifications:
            importance = n.get("importance", "normal")
            subject = n.get("subject", n.get("event", "?"))[:50]
            ts = n.get("timestamp", "")

            if importance == "alert":
                icon = f"{C.RED}●{C.RESET}"
            elif importance == "warning":
                icon = f"{C.YELLOW}●{C.RESET}"
            else:
                icon = f"{C.GREEN}●{C.RESET}"

            choices.append(f"{icon} {subject}  {C.DIM}{ts}{C.RESET}")

        choices.extend(["Dismiss all", "← Back"])
        idx = pick_option(f"Notifications ({len(notifications)}):", choices)

        if choices[idx] == "← Back":
            return
        elif choices[idx] == "Dismiss all":
            if confirm(f"Dismiss all {len(notifications)} notification(s)?"):
                for n in notifications:
                    ssh_run(f"rm -f '{n['file']}'")
                success(f"Dismissed {len(notifications)} notifications.")
        elif idx < len(notifications):
            _show_notification(notifications[idx])


def _show_notification(notif):
    """Show detail for a single notification."""
    print(f"\n  {C.BOLD}Notification{C.RESET}\n")
    for key in ["event", "subject", "description", "importance", "timestamp"]:
        val = notif.get(key, "")
        if val:
            print(f"  {C.BOLD}{key.title()}:{C.RESET} {val}")

    idx = pick_option("", ["Dismiss", "← Back"])
    if idx == 0:
        ssh_run(f"rm -f '{notif['file']}'")
        success("Notification dismissed.")
