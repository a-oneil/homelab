"""Unraid service plugin — dashboard, Docker, VMs, compose, logs, scripts."""

import json
import os
import subprocess
import time
import urllib.request

from homelab.auditlog import log_action
from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.transport import ssh_run
from homelab.ui import (
    C, pick_option, pick_multi, confirm, prompt_text, scrollable_list,
    info, success, error, warn, bar_chart, sparkline, check_tool,
)


def get_host():
    return CFG["unraid_ssh_host"]


def get_base():
    return CFG["unraid_remote_base"]


def _ssh(command, **kwargs):
    """SSH run targeting the Unraid server."""
    return ssh_run(command, host=get_host(), **kwargs)


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
        result = _ssh(cmd, background=True)
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
            "Unraid File Manager": ("manage_files", lambda: manage_files(
                host=get_host(), base_path=get_base(),
                extra_paths=CFG.get("unraid_extra_paths", []),
                trash_path=CFG.get("unraid_trash_path", ""),
            )),
            "Unraid Transfer to Server": ("fetch_and_transfer", lambda: fetch_and_transfer(
                host=get_host(), base_path=get_base(),
                extra_paths=CFG.get("unraid_extra_paths", []),
            )),
            "Unraid Upload Local Files": ("upload_local", lambda: upload_local(
                host=get_host(), base_path=get_base(),
                extra_paths=CFG.get("unraid_extra_paths", []),
            )),
            "Unraid Download from Server": ("download_from_server", lambda: download_from_server(
                host=get_host(), base_path=get_base(),
            )),
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
            "Unraid Trash": ("manage_trash", lambda: manage_trash(
                host=get_host(), trash_path=CFG.get("unraid_trash_path", ""),
            )),
            "Unraid Mount Browser": ("mount_browser", lambda: mount_browser(
                host=get_host(), base_path=get_base(),
            )),
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
    while True:
        idx = pick_option("Unraid:", [
            "Files             — browse, upload, download, trash, mount browser",
            "Docker            — containers, compose, logs, shell",
            "Dashboard         — disk usage, CPU, RAM, SMART, transfers",
            "Server Tools      — SSH shell, live logs, VSCode, user scripts",
            "───────────────",
            "VMs               — start, stop, restart, snapshot",
            "Parity Check      — status, start, stop, schedule",
            "Notifications     — view and dismiss system alerts",
            "───────────────",
            "★ Add to Favorites — pin an action to the main menu",
            "← Back",
        ])
        if idx == 10:
            return
        elif idx in (4, 8):
            continue
        elif idx == 9:
            _add_favorite()
        elif idx == 0:
            _files_menu()
        elif idx == 1:
            docker_menu()
        elif idx == 2:
            server_dashboard()
        elif idx == 3:
            _server_tools_menu()
        elif idx == 5:
            vm_management()
        elif idx == 6:
            _parity_check()
        elif idx == 7:
            _notification_center()


def _files_menu():
    """Submenu for all file-related operations."""
    from homelab.config import local_hostname
    from homelab.files import (
        manage_files, download_from_server,
        manage_trash, mount_browser,
    )
    hostname = local_hostname()
    while True:
        idx = pick_option("Files:", [
            "File Manager         — browse, search, manage files",
            f"Download from Server — pull files to {hostname}",
            "Transfer to Server   — upload local files or download from web",
            "───────────────",
            "Trash                — manage deleted files",
            "Mount Browser        — tree view of shares by size",
            "← Back",
        ])
        if idx == 6:
            return
        elif idx == 3:
            continue
        elif idx == 0:
            manage_files(
                host=get_host(), base_path=get_base(),
                extra_paths=CFG.get("unraid_extra_paths", []),
                trash_path=CFG.get("unraid_trash_path", ""))
        elif idx == 1:
            download_from_server(host=get_host(), base_path=get_base())
        elif idx == 2:
            _transfer_to_server_menu()
        elif idx == 4:
            manage_trash(host=get_host(), trash_path=CFG.get("unraid_trash_path", ""))
        elif idx == 5:
            mount_browser(host=get_host(), base_path=get_base())


def _server_tools_menu():
    """Submenu for SSH shell, live logs, VSCode, and user scripts."""
    while True:
        idx = pick_option("Server Tools:", [
            "SSH Shell      — open a terminal session on the server",
            "Live Logs      — tail syslog or container logs",
            "User Scripts   — browse and run server scripts",
            "Open in VSCode — launch remote workspace",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 0:
            _ssh_shell()
        elif idx == 1:
            live_log_viewer()
        elif idx == 2:
            user_scripts()
        elif idx == 3:
            open_vscode()


def _transfer_to_server_menu():
    """Sub-menu for transferring files to the server."""
    from homelab.files import fetch_and_transfer, upload_local
    idx = pick_option("Transfer to Server:", [
        "Upload local files   — send files from this machine",
        "Download from web    — wget, curl, yt-dlp, git clone",
        "← Back",
    ])
    if idx == 2:
        return
    elif idx == 0:
        upload_local(
            host=get_host(), base_path=get_base(),
            extra_paths=CFG.get("unraid_extra_paths", []))
    elif idx == 1:
        fetch_and_transfer(
            host=get_host(), base_path=get_base(),
            extra_paths=CFG.get("unraid_extra_paths", []))


def _ssh_shell():
    """Open an interactive SSH session to the Unraid server."""
    host = get_host()
    log_action("SSH Shell", f"Unraid ({host})")
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
            "Active Transfers    — running rsync/scp processes",
            "Disk Usage          — space per share/disk with bar charts",
            "SMART Status        — disk health and diagnostics",
            "System Info         — CPU, RAM, uptime, temperature",
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
            _show_active_transfers()
        elif idx == 1:
            _show_disk_usage()
        elif idx == 2:
            _show_smart_status()
        elif idx == 3:
            _show_system_info()


def _show_disk_usage():
    print(f"\n  {C.BOLD}Disk Usage on {get_host()}{C.RESET}\n")

    result = _ssh("df -h /mnt/user 2>/dev/null")
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

    result = _ssh("du -sh /mnt/user/*/ 2>/dev/null | sort -rh | head -20")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}Top shares by size:{C.RESET}")
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t", 1)
            if len(parts) == 2:
                size, path = parts
                name = os.path.basename(path.rstrip("/"))
                print(f"    {C.ACCENT}{size:>8}{C.RESET}  {name}/")
        print()

    result = _ssh("df -h /mnt/disk* /mnt/cache* 2>/dev/null")
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

    result = _ssh("uptime")
    if result.returncode == 0:
        print(f"  {C.BOLD}Uptime:{C.RESET}  {result.stdout.strip()}")

    result = _ssh("grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}CPU:{C.RESET}     {result.stdout.strip()}")

    result = _ssh("nproc")
    if result.returncode == 0:
        print(f"  {C.BOLD}Cores:{C.RESET}   {result.stdout.strip()}")

    result = _ssh("cat /proc/loadavg")
    if result.returncode == 0:
        parts = result.stdout.strip().split()
        print(f"  {C.BOLD}Load:{C.RESET}    {parts[0]} {parts[1]} {parts[2]}  (1m 5m 15m)")

    result = _ssh("free -h | grep Mem")
    if result.returncode == 0:
        parts = result.stdout.strip().split()
        if len(parts) >= 3:
            total = parts[1]
            used = parts[2]
            print(f"  {C.BOLD}RAM:{C.RESET}     {used} / {total}")

    result = _ssh("sensors 2>/dev/null | grep -i 'core 0\\|package\\|cpu' | head -3")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}Temps:{C.RESET}")
        for line in result.stdout.strip().split("\n"):
            print(f"           {line.strip()}")

    result = _ssh("cat /etc/unraid-version 2>/dev/null")
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {C.BOLD}Unraid:{C.RESET}  {result.stdout.strip()}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _show_active_transfers():
    print(f"\n  {C.BOLD}Active Transfers on {get_host()}{C.RESET}\n")

    result = _ssh("ps aux | grep -E 'rsync|scp|rclone' | grep -v grep")
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
    result = _ssh("ls /dev/sd? 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        warn("No SMART-capable disks found.")
        input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
        return
    disks = result.stdout.strip().split()
    for disk in disks:
        disk_name = os.path.basename(disk)
        hr = _ssh(f"smartctl -H {disk} 2>/dev/null | grep 'SMART overall-health'")
        if hr.returncode == 0 and "PASSED" in hr.stdout:
            status = f"{C.GREEN}PASSED{C.RESET}"
        elif hr.returncode == 0:
            status = f"{C.RED}FAILED{C.RESET}"
        else:
            status = f"{C.DIM}N/A{C.RESET}"
        tr = _ssh(f"smartctl -A {disk} 2>/dev/null | grep -i temperature | head -1")
        temp = "?"
        if tr.returncode == 0 and tr.stdout.strip():
            parts = tr.stdout.strip().split()
            if len(parts) >= 10:
                temp = parts[9] + "C"
        print(f"    {disk_name}  {status}  Temp: {temp}")
    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _smart_detail():
    result = _ssh("lsblk -d -o NAME,SIZE,MODEL 2>/dev/null | grep '^sd'")
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
    hr = _ssh(f"smartctl -H /dev/{disk} 2>/dev/null")
    if hr.returncode == 0:
        for line in hr.stdout.strip().split("\n"):
            if "overall-health" in line.lower():
                print(f"  {C.BOLD}Health:{C.RESET} {line.strip()}")
        print()
    ar = _ssh(f"smartctl -A /dev/{disk} 2>/dev/null")
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
            "Containers        — manage, compose, bulk ops, stats",
            "Docker Resources  — images, networks, volumes, prune",
            "Resource Graphs   — CPU/memory trends per container",
            "───────────────",
            "★ Add to Favorites — pin an action to the main menu",
            "← Back",
        ])
        if idx == 5:
            return
        elif idx == 3:
            continue
        elif idx == 4:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
        elif idx == 0:
            _docker_containers_hub()
        elif idx == 1:
            _docker_resources_menu()
        elif idx == 2:
            _docker_resource_graph()


def _docker_containers_hub():
    """Hub view: container list with summary header and action items at bottom."""
    while True:
        result = _ssh(
            "docker ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}\\t{{.Ports}}' 2>/dev/null"
        )
        if result.returncode != 0:
            error("Failed to list containers. Is Docker running?")
            return

        lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        if not lines:
            warn("No containers found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        containers = []
        choices = []
        running = 0
        stopped = 0
        for line in lines:
            parts = line.split("\t")
            name = parts[0] if len(parts) > 0 else "?"
            status = parts[1] if len(parts) > 1 else "?"
            image = parts[2] if len(parts) > 2 else "?"
            containers.append({"name": name, "status": status, "image": image})
            is_up = "Up" in status
            if is_up:
                running += 1
            else:
                stopped += 1
            if "(healthy)" in status:
                icon = f"{C.GREEN}●{C.RESET}"
            elif "(unhealthy)" in status:
                icon = f"{C.RED}●{C.RESET}"
            elif "(starting)" in status:
                icon = f"{C.YELLOW}●{C.RESET}"
            elif is_up:
                icon = f"{C.GREEN}●{C.RESET}"
            else:
                icon = f"{C.RED}●{C.RESET}"
            choices.append(f"{icon} {name:<25} {C.DIM}{image}{C.RESET}")

        header = (
            f"\n  {C.ACCENT}{C.BOLD}Containers{C.RESET}"
            f"  {len(containers)} total  |  {C.GREEN}{running} running{C.RESET}"
            f"  {C.RED}{stopped} stopped{C.RESET}\n")

        # Action items at bottom
        choices.append("───────────────")
        action_start = len(containers) + 1
        choices.append("Compose Projects  — deploy, pull, edit compose files")
        choices.append("Bulk Operations   — select and manage multiple containers")
        choices.append("Docker Stats      — CPU, RAM, network per container")
        choices.append("← Back")

        idx = pick_option("Containers:", choices, header=header)

        if idx == action_start + 3:  # Back
            return
        elif idx == len(containers):  # separator
            continue
        elif idx == action_start:
            docker_compose()
        elif idx == action_start + 1:
            _docker_bulk_ops()
        elif idx == action_start + 2:
            _docker_stats()
        elif idx < len(containers):
            _container_actions(containers[idx]["name"])


def _docker_resources_menu():
    """Submenu for Docker images, networks, volumes, and system prune."""
    while True:
        idx = pick_option("Docker Resources:", [
            "Images       — list and prune images",
            "Networks     — list, inspect, create, remove",
            "Volumes      — list, inspect, prune",
            "System Prune — clean unused resources",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 0:
            _docker_images()
        elif idx == 1:
            _docker_networks()
        elif idx == 2:
            _docker_volumes()
        elif idx == 3:
            _docker_system_prune()


def _docker_stats():
    """Show CPU, RAM, and network usage per container."""
    result = _ssh(
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


def _docker_resource_graph():
    """Show CPU/memory sparkline trends per container."""
    samples = 5
    interval = 2
    print()

    # Collect multiple snapshots
    history = {}  # name -> {"cpu": [], "mem": [], "mem_usage": ""}
    for i in range(samples):
        if i > 0:
            time.sleep(interval)
        print(f"\r  Collecting sample {i + 1}/{samples}...", end="", flush=True)
        result = _ssh(
            "docker stats --no-stream --format "
            "'{{.Name}}\\t{{.CPUPerc}}\\t{{.MemPerc}}\\t{{.MemUsage}}' 2>/dev/null"
        )
        if result.returncode != 0 or not result.stdout.strip():
            continue
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name, cpu_str, mem_str, mem_usage = parts
            if name not in history:
                history[name] = {"cpu": [], "mem": [], "mem_usage": ""}
            try:
                history[name]["cpu"].append(float(cpu_str.rstrip("%")))
            except ValueError:
                history[name]["cpu"].append(0)
            try:
                history[name]["mem"].append(float(mem_str.rstrip("%")))
            except ValueError:
                history[name]["mem"].append(0)
            history[name]["mem_usage"] = mem_usage

    print("\r" + " " * 40 + "\r", end="")  # Clear progress line

    if not history:
        error("Failed to collect Docker stats.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Dynamic name column width (cap at 28, truncate with …)
    max_name = min(28, max(len(n) for n in history.keys()))
    max_name = max(max_name, 9)  # At least "Container" width
    total_w = max_name + 2 + samples + 7 + 2 + samples + 2 + 20

    print(f"  {C.BOLD}Container Resource Trends{C.RESET}  {C.DIM}({samples} samples, {interval}s interval){C.RESET}\n")
    print(f"  {C.DIM}{'Container':<{max_name}}  {'CPU':^{samples + 7}}  {'Memory':^{samples + 20}}{C.RESET}")
    print(f"  {'─' * total_w}")

    for name in sorted(history.keys()):
        data = history[name]
        cpu_vals = data["cpu"]
        mem_vals = data["mem"]
        last_cpu = cpu_vals[-1] if cpu_vals else 0
        mem_usage = data["mem_usage"]

        cpu_spark = sparkline(cpu_vals, width=samples)
        mem_spark = sparkline(mem_vals, width=samples)

        # Truncate long names
        display = name if len(name) <= max_name else name[:max_name - 1] + "…"

        if last_cpu > 50:
            cpu_color = C.RED
        elif last_cpu > 10:
            cpu_color = C.YELLOW
        else:
            cpu_color = C.GREEN

        print(
            f"  {display:<{max_name}}  "
            f"{cpu_color}{cpu_spark}{C.RESET} {last_cpu:>5.1f}%  "
            f"{C.ACCENT}{mem_spark}{C.RESET}  {mem_usage}"
        )

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_images():
    """List Docker images with option to prune."""
    while True:
        result = _ssh(
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
        dangling = _ssh("docker images -f dangling=true -q 2>/dev/null")
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
                log_action("Docker Prune", "dangling images")
                info("Pruning dangling images...")
                _ssh("docker image prune -f", capture=False)
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Prune all" in choice:
            if confirm("Remove ALL unused images? This cannot be undone.", default_yes=False):
                log_action("Docker Prune", "all unused images")
                info("Pruning all unused images...")
                _ssh("docker image prune -a -f", capture=False)
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx < len(images):
            img = images[idx]
            action = pick_option(
                f"Image: {img['repo']} ({img['size']})",
                ["Remove image", "Cancel"],
            )
            if action == 0:
                if confirm(f"Remove {img['repo']}?", default_yes=False):
                    log_action("Docker Remove Image", img['repo'])
                    r = _ssh(f"docker rmi {img['id']}")
                    if r.returncode == 0:
                        success(f"Removed: {img['repo']}")
                    else:
                        error(f"Failed (image may be in use): {r.stderr.strip()}")


def _docker_networks():
    """Manage Docker networks."""
    while True:
        result = _ssh(
            "docker network ls --format '{{.Name}}\\t{{.Driver}}\\t{{.Scope}}\\t{{.ID}}' 2>/dev/null"
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No Docker networks found.")
            return

        networks = []
        for line in result.stdout.strip().split("\n"):
            parts = line.strip().split("\t")
            if len(parts) >= 4:
                networks.append({"name": parts[0], "driver": parts[1], "scope": parts[2], "id": parts[3][:12]})

        choices = [f"{n['name']:<25} {C.DIM}{n['driver']:<10} {n['scope']}{C.RESET}" for n in networks]
        choices.append("Create Network")
        choices.append("← Back")
        idx = pick_option(f"Docker Networks ({len(networks)}):", choices)

        if idx == len(choices) - 1:
            return
        elif idx == len(choices) - 2:
            _docker_network_create()
        elif idx < len(networks):
            _docker_network_detail(networks[idx])


def _docker_network_detail(net):
    """Show detail and actions for a Docker network."""
    result = _ssh(f"docker network inspect {net['name']} 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        error("Could not inspect network.")
        return

    import json
    try:
        data = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError):
        error("Could not parse network info.")
        return

    name = data.get("Name", "?")
    driver = data.get("Driver", "?")
    scope = data.get("Scope", "?")
    subnet = ""
    gateway = ""
    ipam_configs = data.get("IPAM", {}).get("Config", [])
    if ipam_configs:
        subnet = ipam_configs[0].get("Subnet", "")
        gateway = ipam_configs[0].get("Gateway", "")

    containers = data.get("Containers", {})

    lines = []
    lines.append(f"\n  {C.BOLD}{name}{C.RESET}\n")
    lines.append(f"  {C.BOLD}Driver:{C.RESET}  {driver}")
    lines.append(f"  {C.BOLD}Scope:{C.RESET}   {scope}")
    if subnet:
        lines.append(f"  {C.BOLD}Subnet:{C.RESET}  {subnet}")
    if gateway:
        lines.append(f"  {C.BOLD}Gateway:{C.RESET} {gateway}")
    lines.append(f"  {C.BOLD}Containers:{C.RESET} {len(containers)}")
    for cid, cinfo in containers.items():
        cname = cinfo.get("Name", cid[:12])
        ipv4 = cinfo.get("IPv4Address", "")
        lines.append(f"    {C.ACCENT}{cname}{C.RESET}  {C.DIM}{ipv4}{C.RESET}")

    print("\n".join(lines))

    # Only allow removing user-created networks
    builtin = {"bridge", "host", "none"}
    actions = []
    if name not in builtin:
        actions.append("Remove Network")
    actions.append("← Back")
    aidx = pick_option(f"Network: {name}", actions)
    if actions[aidx] == "Remove Network":
        if confirm(f"Remove network {name}?", default_yes=False):
            log_action("Docker Network Remove", name)
            r = _ssh(f"docker network rm {name}")
            if r.returncode == 0:
                success(f"Removed: {name}")
            else:
                error(f"Failed: {r.stderr.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_network_create():
    """Create a new Docker network."""
    name = prompt_text("Network name:")
    if not name:
        return

    driver_idx = pick_option("Network driver:", ["bridge (Recommended)", "overlay", "macvlan", "← Back"])
    if driver_idx == 3:
        return
    drivers = ["bridge", "overlay", "macvlan"]
    driver = drivers[driver_idx]

    subnet = prompt_text("Subnet (optional, e.g. 172.20.0.0/16):")
    subnet_flag = f"--subnet {subnet}" if subnet else ""

    cmd = f"docker network create --driver {driver} {subnet_flag} {name}"
    if confirm(f"Create network: {name} ({driver})?"):
        log_action("Docker Network Create", f"{name} ({driver})")
        r = _ssh(cmd)
        if r.returncode == 0:
            success(f"Created network: {name}")
        else:
            error(f"Failed: {r.stderr.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_volumes():
    """Manage Docker volumes."""
    while True:
        result = _ssh(
            "docker volume ls --format '{{.Name}}\\t{{.Driver}}\\t{{.Mountpoint}}' 2>/dev/null"
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
            _docker_volume_prune()
        elif idx < len(volumes):
            _docker_volume_detail(volumes[idx])


def _docker_volume_detail(vol):
    """Show detail for a Docker volume."""
    result = _ssh(f"docker volume inspect {vol['name']} 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        error("Could not inspect volume.")
        return

    import json
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

    # Get disk usage
    du_result = _ssh(f"du -sh {mountpoint} 2>/dev/null")
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
            log_action("Docker Volume Remove", name)
            r = _ssh(f"docker volume rm {name}")
            if r.returncode == 0:
                success(f"Removed: {name}")
            else:
                error(f"Failed: {r.stderr.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_volume_prune():
    """Prune unused Docker volumes."""
    if not confirm("Remove all unused volumes? This cannot be undone.", default_yes=False):
        return
    log_action("Docker Volume Prune", "unused volumes")
    r = _ssh("docker volume prune -f 2>/dev/null")
    if r.returncode == 0:
        success("Pruned unused volumes.")
        if r.stdout.strip():
            print(f"  {C.DIM}{r.stdout.strip()}{C.RESET}")
    else:
        error(f"Failed: {r.stderr.strip()}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_system_prune():
    """Clean up unused Docker resources."""
    result = _ssh("docker system df 2>/dev/null")
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
            log_action("Docker System Prune", "basic")
            info("Pruning...")
            _ssh("docker system prune -f", capture=False)
            print()
            success("Basic prune complete.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif idx == 1:
        if confirm("Run FULL prune? This removes ALL unused images and volumes.", default_yes=False):
            log_action("Docker System Prune", "full (all images + volumes)")
            info("Pruning everything...")
            _ssh("docker system prune -a --volumes -f", capture=False)
            print()
            success("Full prune complete.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_bulk_ops():
    """Bulk operations on Docker containers."""
    result = _ssh(
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

    hdr = f"\n  {C.BOLD}Containers:{C.RESET} {len(running)} running, {len(stopped)} stopped\n"

    idx = pick_option("Bulk Operation:", [
        "Select containers to stop",
        "Select containers to restart",
        "Select containers to start",
        "Select containers to update   — pull & restart",
        "───────────────",
        f"Stop ALL running ({len(running)})",
        f"Restart ALL running ({len(running)})",
        f"Start ALL stopped ({len(stopped)})",
        f"Update ALL running ({len(running)})  — pull & restart",
        "← Back",
    ], header=hdr)
    if idx == 9:
        return
    elif idx == 4:
        return _docker_bulk_ops()
    elif idx == 0 and running:
        choices = [f"{n:<25} [running]" for n in running]
        selected = pick_multi("Select containers to stop:", choices)
        if not selected:
            return
        names = [running[i] for i in selected]
        if confirm(f"Stop {len(names)} container(s)?"):
            log_action("Docker Bulk Stop", f"{len(names)} containers")
            for i, name in enumerate(names):
                info(f"Stopping {name}... ({i + 1}/{len(names)})")
                _ssh(f"docker stop {name}")
            success(f"Stopped {len(names)} containers.")
    elif idx == 1 and running:
        choices = [f"{n:<25} [running]" for n in running]
        selected = pick_multi("Select containers to restart:", choices)
        if not selected:
            return
        names = [running[i] for i in selected]
        if confirm(f"Restart {len(names)} container(s)?"):
            log_action("Docker Bulk Restart", f"{len(names)} containers")
            for i, name in enumerate(names):
                info(f"Restarting {name}... ({i + 1}/{len(names)})")
                _ssh(f"docker restart {name}")
            success(f"Restarted {len(names)} containers.")
    elif idx == 2 and stopped:
        choices = [f"{n:<25} [stopped]" for n in stopped]
        selected = pick_multi("Select containers to start:", choices)
        if not selected:
            return
        names = [stopped[i] for i in selected]
        if confirm(f"Start {len(names)} container(s)?"):
            log_action("Docker Bulk Start", f"{len(names)} containers")
            for i, name in enumerate(names):
                info(f"Starting {name}... ({i + 1}/{len(names)})")
                _ssh(f"docker start {name}")
            success(f"Started {len(names)} containers.")
    elif idx == 3 and running:
        _docker_bulk_update(running)
    elif idx == 5 and running:
        if confirm(f"Stop {len(running)} running container(s)?"):
            log_action("Docker Bulk Stop", f"all {len(running)} running")
            for name in running:
                info(f"Stopping {name}...")
                _ssh(f"docker stop {name}")
            success(f"Stopped {len(running)} containers.")
    elif idx == 6 and running:
        if confirm(f"Restart {len(running)} running container(s)?"):
            log_action("Docker Bulk Restart", f"all {len(running)} running")
            for name in running:
                info(f"Restarting {name}...")
                _ssh(f"docker restart {name}")
            success(f"Restarted {len(running)} containers.")
    elif idx == 7 and stopped:
        if confirm(f"Start {len(stopped)} stopped container(s)?"):
            log_action("Docker Bulk Start", f"all {len(stopped)} stopped")
            for name in stopped:
                info(f"Starting {name}...")
                _ssh(f"docker start {name}")
            success(f"Started {len(stopped)} containers.")
    elif idx == 8 and running:
        _docker_bulk_update(running, select=False)
    else:
        info("No containers to operate on.")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _docker_bulk_update(running, select=True):
    """Pull latest images and restart containers in bulk."""
    if select:
        choices = [f"{n:<25} [running]" for n in running]
        selected = pick_multi("Select containers to update:", choices)
        if not selected:
            return
        names = [running[i] for i in selected]
    else:
        names = list(running)

    if not confirm(f"Pull & restart {len(names)} container(s)? This may cause downtime."):
        return

    # Get image for each container
    images = {}
    for name in names:
        r = _ssh(f"docker inspect {name} --format '{{{{.Config.Image}}}}' 2>/dev/null")
        if r.returncode == 0 and r.stdout.strip():
            images[name] = r.stdout.strip()

    if not images:
        error("Could not determine images for selected containers.")
        return

    # Pull unique images first
    unique_images = list(set(images.values()))
    log_action("Docker Bulk Update", f"{len(names)} containers ({len(unique_images)} images)")
    info(f"Pulling {len(unique_images)} unique image(s)...")
    for i, img in enumerate(unique_images):
        info(f"  Pulling {img}... ({i + 1}/{len(unique_images)})")
        subprocess.run(["ssh", "-t", get_host(), f"docker pull {img}"])

    # Restart containers
    for i, name in enumerate(names):
        info(f"Restarting {name}... ({i + 1}/{len(names)})")
        _ssh(f"docker stop {name}")
        _ssh(f"docker start {name}")

    success(f"Updated {len(names)} containers.")


def _container_actions(name):
    """Show actions for a single Docker container (used by list and favorites)."""
    result = _ssh(
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
        log_action("Docker Stop", name)
        info(f"Stopping {name}...")
        _ssh(f"docker stop {name}", capture=False)
    elif action_label == "Start":
        log_action("Docker Start", name)
        info(f"Starting {name}...")
        _ssh(f"docker start {name}", capture=False)
    elif action_label == "Restart":
        log_action("Docker Restart", name)
        info(f"Restarting {name}...")
        _ssh(f"docker restart {name}", capture=False)
    elif action_label == "Logs (last 50 lines)":
        print(f"\n  {C.BOLD}Last 50 lines of {name}:{C.RESET}\n")
        _ssh(f"docker logs --tail 50 {name}", capture=False)
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif action_label == "Live logs (tail -f)":
        log_action("Docker Logs", name)
        info(f"Tailing logs for {name} (Ctrl+C to exit)...")
        try:
            subprocess.run(["ssh", "-t", get_host(), f"docker logs -f --tail 100 {name}"])
        except KeyboardInterrupt:
            print()
    elif action_label == "Shell":
        log_action("Docker Shell", name)
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
        check = _ssh(f"test -d '{appdata_path}' && echo 'exists'")
        if check.returncode == 0 and "exists" in check.stdout:
            from homelab.files import manage_files_at
            manage_files_at(appdata_path, host=get_host(), base_path=get_base())
        else:
            warn(f"Appdata folder not found: {appdata_path}")


def _container_inspect(name):
    """Show detailed info for a container."""
    result = _ssh(
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
    ports_r = _ssh(
        f"docker inspect {name} --format '{{{{range $p, $conf := .NetworkSettings.Ports}}}}"
        f"{{{{$p}}}} -> {{{{range $conf}}}}{{{{.HostPort}}}}{{{{end}}}}\\n{{{{end}}}}' 2>/dev/null"
    )
    if ports_r.returncode == 0 and ports_r.stdout.strip():
        print(f"\n  {C.BOLD}Ports:{C.RESET}")
        for line in ports_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Volumes/Mounts
    mounts_r = _ssh(
        f"docker inspect {name} --format '{{{{range .Mounts}}}}"
        f"{{{{.Source}}}} -> {{{{.Destination}}}} ({{{{.Type}}}})\\n{{{{end}}}}' 2>/dev/null"
    )
    if mounts_r.returncode == 0 and mounts_r.stdout.strip():
        print(f"\n  {C.BOLD}Mounts:{C.RESET}")
        for line in mounts_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Networks
    nets_r = _ssh(
        f"docker inspect {name} --format '{{{{range $k, $v := .NetworkSettings.Networks}}}}"
        f"{{{{$k}}}} ({{{{$v.IPAddress}}}})\\n{{{{end}}}}' 2>/dev/null"
    )
    if nets_r.returncode == 0 and nets_r.stdout.strip():
        print(f"\n  {C.BOLD}Networks:{C.RESET}")
        for line in nets_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Environment variables (filtered for secrets)
    env_r = _ssh(
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
    img_r = _ssh(
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

    log_action("Docker Update", f"{name} ({image})")
    info(f"Pulling {image}...")
    subprocess.run(["ssh", "-t", get_host(), f"docker pull {image}"])

    info(f"Restarting {name}...")
    _ssh(f"docker stop {name}")
    _ssh(f"docker start {name}")
    success(f"Updated and restarted: {name}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _container_remove(name):
    """Remove a stopped container."""
    if not confirm(f"Remove container {name}?", default_yes=False):
        return
    log_action("Docker Remove", name)
    r = _ssh(f"docker rm {name}")
    if r.returncode == 0:
        success(f"Removed: {name}")
    else:
        error(f"Failed: {r.stderr.strip()}")


def docker_containers():
    while True:
        result = _ssh(
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
            is_up = "Up" in status
            if "(healthy)" in status:
                icon = f"{C.GREEN}●{C.RESET}"
            elif "(unhealthy)" in status:
                icon = f"{C.RED}●{C.RESET}"
            elif "(starting)" in status:
                icon = f"{C.YELLOW}●{C.RESET}"
            elif is_up:
                icon = f"{C.GREEN}●{C.RESET}"
            else:
                icon = f"{C.RED}●{C.RESET}"
            choices.append(f"{icon} {name:<25} {C.DIM}{image}{C.RESET}")

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
        result = _ssh(
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
        choices.append("Git                  — pull, commit, push")
        choices.append("★ Add to Favorites")
        choices.append("← Back")
        idx = pick_option("Docker Compose projects:", choices)
        n = len(project_names)
        if idx == n + 4:
            return
        elif idx == n + 3:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
            continue
        elif idx == n + 2:
            _git_menu(projects_path, "compose projects")
        elif idx == n + 1:
            continue  # separator
        elif idx == n:
            _create_compose_project(projects_path)
        else:
            _manage_compose_project(project_names[idx], project_paths[idx])


def _git_menu(path, label):
    """Git operations for a remote directory on Unraid."""
    check = _ssh(f"cd '{path}' && git rev-parse --is-inside-work-tree 2>/dev/null")
    if check.returncode != 0 or "true" not in check.stdout:
        warn("Not a git repository.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        status = _ssh(f"cd '{path}' && git status --short 2>/dev/null")
        branch_r = _ssh(f"cd '{path}' && git branch --show-current 2>/dev/null")
        branch_name = branch_r.stdout.strip() if branch_r.returncode == 0 else "?"

        hdr_lines = [f"\n  {C.BOLD}Git: {label}{C.RESET}"]
        hdr_lines.append(f"  Branch: {C.ACCENT}{branch_name}{C.RESET}")
        if status.stdout.strip():
            changes = status.stdout.strip().split("\n")
            hdr_lines.append(f"  {C.YELLOW}{len(changes)} changed file(s){C.RESET}")
            for line in changes[:10]:
                hdr_lines.append(f"    {C.DIM}{line.strip()}{C.RESET}")
            if len(changes) > 10:
                hdr_lines.append(f"    {C.DIM}... and {len(changes) - 10} more{C.RESET}")
        else:
            hdr_lines.append(f"  {C.GREEN}Working tree clean{C.RESET}")
        hdr_lines.append("")

        idx = pick_option("", [
            "Git Pull             — fetch and merge from remote",
            "Git Commit & Push    — stage, commit, and push all changes",
            "Git Log              — last 10 commits",
            "← Back",
        ], header="\n".join(hdr_lines))

        if idx == 3:
            return
        elif idx == 0:
            # Fix permissions if needed
            check = _ssh(f"test -w '{path}/.git/objects' && echo 'ok'")
            if "ok" not in check.stdout:
                info("Fixing .git permissions...")
                _ssh(f"chown -R $(whoami):$(id -gn) '{path}/.git'")
            info("Pulling...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && git pull"])
            log_action("Git Pull", label)
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx == 1:
            _git_commit_push(path, label)
        elif idx == 2:
            r = _ssh(f"cd '{path}' && git log --oneline -10 2>/dev/null")
            lines = r.stdout.strip().split("\n") if r.stdout.strip() else ["(no commits)"]
            scrollable_list(f"Git Log: {label}", lines)


def _git_commit_push(path, label):
    """Stage all changes, commit with a message, and push on Unraid."""
    status = _ssh(f"cd '{path}' && git status --short 2>/dev/null")
    if not status.stdout.strip():
        warn("No changes to commit.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Ensure git user.name and user.email are set
    name_r = _ssh(f"cd '{path}' && git config user.name 2>/dev/null")
    email_r = _ssh(f"cd '{path}' && git config user.email 2>/dev/null")
    if not name_r.stdout.strip():
        git_name = prompt_text("Git user.name (not set on remote):")
        if not git_name:
            return
        _ssh(f"cd '{path}' && git config user.name '{git_name}'")
    if not email_r.stdout.strip():
        git_email = prompt_text("Git user.email (not set on remote):")
        if not git_email:
            return
        _ssh(f"cd '{path}' && git config user.email '{git_email}'")

    msg = prompt_text("Commit message:")
    if not msg:
        return

    safe_msg = msg.replace("'", "'\\''")
    # Fix permissions if needed
    check = _ssh(f"test -w '{path}/.git/objects' && echo 'ok'")
    if "ok" not in check.stdout:
        info("Fixing .git permissions...")
        _ssh(f"chown -R $(whoami):$(id -gn) '{path}/.git'")
    info("Committing and pushing...")
    subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && git add -A && git commit -m '{safe_msg}' && git push"])
    log_action("Git Commit & Push", f"{label}: {msg[:60]}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _manage_compose_project(name, path):
    """Manage a single compose project."""
    compose_file = f"{path}/docker-compose.yml"
    check = _ssh(f"test -f '{compose_file}' && echo 'exists'")
    if check.returncode != 0 or "exists" not in check.stdout:
        compose_file = f"{path}/compose.yaml"
        check = _ssh(f"test -f '{compose_file}' && echo 'exists'")
        if check.returncode != 0 or "exists" not in check.stdout:
            error(f"No docker-compose.yml found in {name}")
            return
    env_file = f"{path}/.env"
    while True:
        hdr = f"\n  {C.BOLD}Project: {name}{C.RESET}\n"

        # Fetch containers for inline display
        ct_result = _ssh(
            f"cd '{path}' && docker-compose ps --format "
            f"'{{{{.Name}}}}\\t{{{{.Status}}}}\\t{{{{.Image}}}}\\t{{{{.Ports}}}}' 2>/dev/null"
        )
        containers = []
        choices = []
        if ct_result.returncode == 0 and ct_result.stdout.strip():
            for line in ct_result.stdout.strip().split("\n"):
                parts = line.strip().split("\t")
                if not parts:
                    continue
                cname = parts[0]
                cstatus = parts[1] if len(parts) > 1 else "?"
                cimage = parts[2] if len(parts) > 2 else "?"
                cports = parts[3] if len(parts) > 3 else ""
                containers.append({"name": cname, "status": cstatus, "image": cimage, "ports": cports})
                is_up = "Up" in cstatus
                if "(healthy)" in cstatus:
                    icon = f"{C.GREEN}●{C.RESET}"
                elif "(unhealthy)" in cstatus:
                    icon = f"{C.RED}●{C.RESET}"
                elif "(starting)" in cstatus:
                    icon = f"{C.YELLOW}●{C.RESET}"
                elif is_up:
                    icon = f"{C.GREEN}●{C.RESET}"
                else:
                    icon = f"{C.RED}●{C.RESET}"
                choices.append(f"{icon} {cname:<25} {C.DIM}{cimage}{C.RESET}")

        choices.append("───────────────")
        action_start = len(containers) + 1
        choices.extend([
            "Up (deploy)", "Down (stop)", "Pull & Up (update)",
            "Logs (follow)      — view and follow service logs",
            "Restart service    — restart a single service",
            "───────────────",
            "Edit compose file", "Edit .env file",
            "Validate           — check compose syntax",
            "Delete project     — remove project directory",
            "───────────────",
            "★ Favorite", "← Back",
        ])
        idx = pick_option("", choices, header=hdr)

        if idx < len(containers):
            _compose_container_actions(path, name, containers[idx])
            continue
        elif idx == len(containers) or idx == action_start + 5 or idx == action_start + 10:
            continue  # separators

        choice = choices[idx]
        if choice == "← Back":
            return
        elif choice == "★ Favorite":
            from homelab.plugins import add_item_favorite
            add_item_favorite("docker_compose", name, f"Compose: {name}")
        elif choice == "Edit compose file":
            subprocess.run(["ssh", "-t", get_host(), f"nano '{compose_file}'"])
        elif choice == "Edit .env file":
            _ssh(f"touch '{env_file}'")
            subprocess.run(["ssh", "-t", get_host(), f"nano '{env_file}'"])
        elif "Validate" in choice:
            info("Validating compose file...")
            r = _ssh(f"cd '{path}' && docker-compose config 2>&1")
            if r.returncode == 0:
                success("Compose file is valid.")
            else:
                error("Validation failed:")
                print(f"\n{r.stdout.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Delete project" in choice:
            if confirm(f"Delete project '{name}' and all its files?", default_yes=False):
                log_action("Compose Delete", name)
                r = _ssh(f"cd '{path}' && docker-compose down 2>/dev/null; rm -rf '{path}'")
                if r.returncode == 0:
                    success(f"Deleted project: {name}")
                else:
                    error(f"Failed: {r.stderr.strip()}")
                return
        elif "Restart service" in choice:
            _compose_restart_service(path, name)
        elif choice == "Up (deploy)":
            log_action("Compose Up", name)
            info("Deploying...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose up -d"])
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Down (stop)":
            log_action("Compose Down", name)
            info("Stopping...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose down"])
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Pull & Up (update)":
            log_action("Compose Update", name)
            info("Pulling and deploying...")
            subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose pull && docker-compose up -d"])
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Logs" in choice:
            _compose_logs(path, name)


def _compose_container_actions(path, project_name, container):
    """Actions for a single container in a compose project."""
    cname = container["name"]
    is_running = "Up" in container.get("status", "")

    while True:
        hdr = (
            f"\n  {C.BOLD}{cname}{C.RESET}\n"
            f"  {C.BOLD}Image:{C.RESET}  {container.get('image', '?')}\n"
            f"  {C.BOLD}Status:{C.RESET} {container.get('status', '?')}\n"
        )
        if container.get("ports"):
            hdr += f"  {C.BOLD}Ports:{C.RESET}  {container['ports']}\n"

        actions = []
        if is_running:
            actions.extend(["Stop", "Restart", "Logs (follow)", "Shell"])
        else:
            actions.extend(["Start", "Logs (last 100 lines)"])
        actions.extend(["Inspect", "← Back"])

        aidx = pick_option("", actions, header=hdr)
        action = actions[aidx]

        if action == "← Back":
            return
        elif action == "Stop":
            log_action("Compose Container Stop", f"{project_name}/{cname}")
            _ssh(f"docker stop {cname}")
            success(f"Stopped: {cname}")
            return
        elif action == "Start":
            log_action("Compose Container Start", f"{project_name}/{cname}")
            _ssh(f"docker start {cname}")
            success(f"Started: {cname}")
            return
        elif action == "Restart":
            log_action("Compose Container Restart", f"{project_name}/{cname}")
            _ssh(f"docker restart {cname}")
            success(f"Restarted: {cname}")
            return
        elif action == "Logs (follow)":
            subprocess.run(["ssh", "-t", get_host(), f"docker logs -f --tail 50 {cname}"])
        elif action == "Logs (last 100 lines)":
            r = _ssh(f"docker logs --tail 100 {cname} 2>&1")
            if r.stdout.strip():
                lines = r.stdout.strip().split("\n")
                scrollable_list(f"Logs: {cname}", lines)
            else:
                warn("No log output.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif action == "Shell":
            subprocess.run(["ssh", "-t", get_host(), f"docker exec -it {cname} /bin/sh"])
        elif action == "Inspect":
            r = _ssh(f"docker inspect {cname} --format '"
                     f"ID: {{{{.Id}}}}\\n"
                     f"Created: {{{{.Created}}}}\\n"
                     f"RestartCount: {{{{.RestartCount}}}}\\n"
                     f"Platform: {{{{.Platform}}}}\\n"
                     f"' 2>/dev/null")
            if r.returncode == 0 and r.stdout.strip():
                print(f"\n  {C.BOLD}{cname}{C.RESET}\n")
                for line in r.stdout.strip().split("\n"):
                    if line.strip():
                        print(f"  {line.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _compose_restart_service(path, project_name):
    """Restart a single service within a compose project."""
    result = _ssh(
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
    log_action("Compose Restart Service", f"{project_name}/{svc}")
    info(f"Restarting {svc}...")
    subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose restart {svc}"])
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _compose_logs(path, project_name):
    """Follow logs for all or a specific service in a compose project."""
    result = _ssh(
        f"cd '{path}' && docker-compose config --services 2>/dev/null"
    )
    services = []
    if result.returncode == 0 and result.stdout.strip():
        services = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]

    choices = [
        "Follow all services",
        "View last 100 lines  — all services",
    ]
    if services:
        choices.append("Follow a service     — select one to tail")
        choices.append("View service logs     — last 100 lines of one service")
    choices.append("← Back")

    idx = pick_option(f"Logs: {project_name}", choices)
    choice = choices[idx]

    if choice == "← Back":
        return
    elif choice == "Follow all services":
        subprocess.run(["ssh", "-t", get_host(), f"cd '{path}' && docker-compose logs -f --tail 50"])
    elif "View last 100" in choice and "all" in choice:
        r = _ssh(f"cd '{path}' && docker-compose logs --tail 100 2>&1")
        if r.stdout.strip():
            lines = r.stdout.strip().split("\n")
            scrollable_list(f"Logs: {project_name}", lines)
        else:
            warn("No log output.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif "Follow a service" in choice:
        svc_choices = services + ["← Back"]
        sidx = pick_option("Follow logs for:", svc_choices)
        if sidx < len(services):
            subprocess.run(["ssh", "-t", get_host(),
                            f"cd '{path}' && docker-compose logs -f --tail 50 {services[sidx]}"])
    elif "View service logs" in choice:
        svc_choices = services + ["← Back"]
        sidx = pick_option("View logs for:", svc_choices)
        if sidx < len(services):
            r = _ssh(f"cd '{path}' && docker-compose logs --tail 100 {services[sidx]} 2>&1")
            if r.stdout.strip():
                lines = r.stdout.strip().split("\n")
                scrollable_list(f"Logs: {project_name}/{services[sidx]}", lines)
            else:
                warn("No log output.")
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
    check = _ssh(f"test -d '{project_dir}' && echo 'exists'")
    if check.returncode == 0 and "exists" in check.stdout:
        error(f"Project '{name}' already exists.")
        return
    _ssh(f"mkdir -p '{project_dir}'")
    compose_file = f"{project_dir}/docker-compose.yml"
    _ssh(f"echo 'version: \"3\"\\nservices:\\n' > '{compose_file}'")
    success(f"Created project: {name}")
    info("Opening compose file for editing...")
    subprocess.run(["ssh", "-t", get_host(), f"nano '{compose_file}'"])


# ─── VM Management ─────────────────────────────────────────────────────────

def _vm_actions(name):
    """Show actions for a single VM (used by list and favorites)."""
    result = _ssh(f"virsh domstate {name} 2>/dev/null")
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
        log_action("VM Start", name)
        info(f"Starting {name}...")
        _ssh(f"virsh start {name}", capture=False)
    elif action_label == "Shutdown":
        log_action("VM Shutdown", name)
        info(f"Shutting down {name}...")
        _ssh(f"virsh shutdown {name}", capture=False)
    elif action_label == "Force off":
        if confirm(f"Force off {name}?", default_yes=False):
            log_action("VM Force Off", name)
            _ssh(f"virsh destroy {name}", capture=False)
    elif action_label == "Restart":
        log_action("VM Restart", name)
        info(f"Restarting {name}...")
        _ssh(f"virsh shutdown {name}", capture=False)
        info("Waiting for shutdown...")
        time.sleep(5)
        _ssh(f"virsh start {name}", capture=False)
    elif action_label == "Snapshot":
        snap_name = prompt_text("Snapshot name:") or f"snap_{int(time.time())}"
        log_action("VM Snapshot", f"{name} → {snap_name}")
        info(f"Creating snapshot '{snap_name}'...")
        r = _ssh(f"virsh snapshot-create-as {name} {snap_name}")
        if r.returncode == 0:
            success(f"Snapshot created: {snap_name}")
        else:
            error(f"Failed: {r.stderr.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def vm_management():
    """Manage VMs via virsh (SSH) or GraphQL if available."""
    while True:
        # Try virsh
        result = _ssh("virsh list --all 2>/dev/null")
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
            "Multiple containers   — combined live logs",
            "───────────────",
            "★ Add to Favorites",
            "← Back",
        ])
        if idx == 5:
            return
        elif idx == 3:
            continue
        elif idx == 4:
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
        elif idx == 2:
            _multi_container_log()


def _get_running_containers():
    """Get list of running container names and display choices."""
    result = _ssh("docker ps --format '{{.Names}}\\t{{.Status}}' 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        return [], []
    lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    containers = []
    choices = []
    for line in lines:
        parts = line.split("\t")
        name = parts[0] if parts else "?"
        status = parts[1] if len(parts) > 1 else "?"
        containers.append(name)
        choices.append(f"{name} [{status}]")
    return containers, choices


def _live_docker_log():
    containers, choices = _get_running_containers()
    if not containers:
        error("No running containers found.")
        return
    choices.append("← Back")
    idx = pick_option("Select container:", choices)
    if idx >= len(containers):
        return
    container = containers[idx]

    action = pick_option(f"Logs: {container}", [
        "Follow (live)         — tail -f, Ctrl+C to exit",
        "Last 50 lines         — static view",
        "Last 200 lines        — static view",
        "Search logs           — grep for a term",
        "← Back",
    ])
    if action == 4:
        return
    elif action == 0:
        info(f"Viewing logs for {container} (Ctrl+C to exit)...")
        try:
            subprocess.run(["ssh", "-t", get_host(), f"docker logs -f --tail 100 {container}"])
        except KeyboardInterrupt:
            print()
    elif action == 1:
        print(f"\n  {C.BOLD}Last 50 lines of {container}:{C.RESET}\n")
        _ssh(f"docker logs --tail 50 {container} 2>&1", capture=False)
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif action == 2:
        print(f"\n  {C.BOLD}Last 200 lines of {container}:{C.RESET}\n")
        _ssh(f"docker logs --tail 200 {container} 2>&1", capture=False)
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif action == 3:
        term = prompt_text("Search term:")
        if not term:
            return
        result = _ssh(f"docker logs {container} 2>&1 | grep -i '{term}' | tail -50")
        if result.returncode != 0 or not result.stdout.strip():
            warn(f"No matches for '{term}' in {container} logs.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return
        rows = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        scrollable_list(f"Search: '{term}' in {container} ({len(rows)} matches):", rows)


def _multi_container_log():
    """Follow logs from multiple containers simultaneously."""
    containers, choices = _get_running_containers()
    if not containers:
        error("No running containers found.")
        return
    selected = pick_multi("Select containers to follow:", choices)
    if not selected:
        return
    names = [containers[i] for i in selected]
    if not confirm(f"Follow logs for {len(names)} container(s)?"):
        return
    # Build a command that tails all selected containers with prefixed output
    parts = []
    for name in names:
        parts.append(f"docker logs -f --tail 20 {name} 2>&1 | sed 's/^/[{name}] /' &")
    cmd = " ".join(parts) + " wait"
    info(f"Following {len(names)} containers (Ctrl+C to exit)...")
    try:
        subprocess.run(["ssh", "-t", get_host(), cmd])
    except KeyboardInterrupt:
        print()


# ─── User Scripts ───────────────────────────────────────────────────────────

def _create_user_script():
    """Create a new user script on Unraid."""
    scripts_path = "/boot/config/plugins/user.scripts/scripts"
    dir_name = prompt_text("  Script directory name (lowercase, dashes):")
    if not dir_name:
        return
    dir_name = dir_name.strip().lower().replace(" ", "-")
    display_name = prompt_text("  Display name:", default=dir_name)
    description = prompt_text("  Description (optional):")
    script_dir = f"{scripts_path}/{dir_name}"
    check = _ssh(f"test -d '{script_dir}' && echo 'exists'")
    if "exists" in (check.stdout or ""):
        error(f"Script '{dir_name}' already exists.")
        return
    _ssh(f"mkdir -p '{script_dir}'")
    _ssh(f"cat > '{script_dir}/name' << 'EOF'\n{display_name}\nEOF")
    if description:
        _ssh(f"cat > '{script_dir}/description' << 'EOF'\n{description}\nEOF")
    _ssh(f"cat > '{script_dir}/script' << 'EOF'\n#!/bin/bash\n\nEOF")
    _ssh(f"chmod +x '{script_dir}/script'")
    success(f"Created script '{dir_name}'")
    if confirm("  Open in editor now?"):
        subprocess.run(["ssh", "-t", get_host(), f"nano '{script_dir}/script'"])


def user_scripts():
    """Browse and run Unraid user scripts."""
    scripts_path = "/boot/config/plugins/user.scripts/scripts"
    while True:
        result = _ssh(
            f"find '{scripts_path}' -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort"
        )
        if result.returncode != 0 or not result.stdout.strip():
            script_paths = []
            script_names = []
        else:
            script_paths = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
            script_names = [os.path.basename(p) for p in script_paths]
        schedules = _load_all_schedules() if script_paths else {}
        display_names = []
        for sp, sn in zip(script_paths, script_names):
            freq, custom = _get_script_schedule(sp, schedules)
            tag = _format_schedule_tag(freq, custom)
            display_names.append(f"{sn}  [{tag}]" if tag else sn)
        choices = display_names + [
            "───────────────", "+ New Script",
            "Git                  — pull, commit, push",
            "★ Add to Favorites", "← Back",
        ]
        idx = pick_option("User Scripts:", choices)
        n = len(script_names)
        if idx == n + 4:
            return
        elif idx == n + 3:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(UnraidPlugin())
            continue
        elif idx == n + 2:
            _git_menu(scripts_path, "user scripts")
        elif idx == n + 1:
            _create_user_script()
            continue
        elif idx == n:
            continue  # separator
        if idx >= len(script_names):
            continue
        _manage_user_script(script_names[idx], script_paths[idx])


def _load_all_schedules():
    """Load all schedules from schedule.json in one SSH call."""
    schedule_file = "/boot/config/plugins/user.scripts/schedule.json"
    r = _ssh(f"cat '{schedule_file}' 2>/dev/null")
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def _get_script_schedule(script_path, schedules=None):
    """Read schedule for a script. Pass schedules dict to avoid extra SSH call."""
    if schedules is None:
        schedules = _load_all_schedules()
    key = f"{script_path}/script"
    if key in schedules:
        return schedules[key].get("frequency", "disabled"), schedules[key].get("custom", "")
    return "disabled", ""


def _format_schedule_tag(freq, custom):
    """Return a short schedule tag string, or empty if disabled."""
    if freq == "disabled":
        return ""
    label = SCHEDULE_LABELS.get(freq, freq)
    if freq == "custom" and custom:
        label = custom
    return label


def _set_script_schedule(script_path, frequency, custom=""):
    """Write schedule for a script to schedule.json."""
    schedule_file = "/boot/config/plugins/user.scripts/schedule.json"
    r = _ssh(f"cat '{schedule_file}' 2>/dev/null")
    try:
        schedules = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    except json.JSONDecodeError:
        schedules = {}
    key = f"{script_path}/script"
    script_name = os.path.basename(script_path)
    schedules[key] = {
        "script": key,
        "frequency": frequency,
        "id": f"schedule{script_name}",
        "custom": custom,
    }
    payload = json.dumps(schedules, indent=4)
    _ssh(f"cat > '{schedule_file}' << 'SCHEDEOF'\n{payload}\nSCHEDEOF")


SCHEDULE_LABELS = {
    "disabled": "Disabled",
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "boot": "At First Array Start Only",
    "start": "At Every Array Start",
    "custom": "Custom Cron",
}


def _edit_script_schedule(script_path):
    """Interactive schedule editor for a user script."""
    freq, custom = _get_script_schedule(script_path)
    current = SCHEDULE_LABELS.get(freq, freq)
    if freq == "custom" and custom:
        current += f" ({custom})"
    hdr = f"\n  {C.BOLD}Current schedule:{C.RESET} {current}\n"
    options = list(SCHEDULE_LABELS.values()) + ["← Back"]
    idx = pick_option("Set schedule:", options, header=hdr)
    if idx == len(options) - 1:
        return
    new_freq = list(SCHEDULE_LABELS.keys())[idx]
    new_custom = ""
    if new_freq == "custom":
        new_custom = prompt_text("  Cron expression (e.g. 0 3 * * 4):")
        if not new_custom:
            warn("No cron expression entered.")
            return
    elif new_freq == "weekly":
        new_custom = "0 8 * * 5"
    elif new_freq == "boot":
        new_custom = "@reboot"
    _set_script_schedule(script_path, new_freq, new_custom)
    success(f"Schedule set to {SCHEDULE_LABELS[new_freq]}" + (f" ({new_custom})" if new_custom else ""))


def _manage_user_script(name, path):
    script_file = f"{path}/script"
    check = _ssh(f"test -f '{script_file}' && echo 'exists'")
    if check.returncode != 0 or "exists" not in check.stdout:
        error(f"No script file found for {name}")
        return
    while True:
        desc_r = _ssh(f"cat '{path}/description' 2>/dev/null")
        description = desc_r.stdout.strip() if desc_r.returncode == 0 else ""
        freq, custom = _get_script_schedule(path)
        sched_label = SCHEDULE_LABELS.get(freq, freq)
        if freq == "custom" and custom:
            sched_label += f" ({custom})"
        hdr = f"\n  {C.BOLD}Script: {name}{C.RESET}\n"
        if description:
            hdr += f"  {C.DIM}{description[:80]}{C.RESET}\n"
        hdr += f"  {C.DIM}Schedule: {sched_label}{C.RESET}\n"
        idx = pick_option("", [
            "Run script", "View source", "Edit script", "Edit schedule",
            "← Back",
        ], header=hdr)
        if idx == 4:
            return
        elif idx == 0:
            log_action("User Script Run", name)
            info(f"Running {name}...")
            print()
            subprocess.run(["ssh", "-t", get_host(), f"bash '{script_file}'"])
            print()
            input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx == 1:
            print(f"\n  {C.BOLD}Source: {name}{C.RESET}\n")
            r = _ssh(f"cat '{script_file}'")
            if r.returncode == 0:
                print(r.stdout)
            else:
                error("Could not read script file.")
            print()
            input(f"  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx == 2:
            subprocess.run(["ssh", "-t", get_host(), f"nano '{script_file}'"])
        elif idx == 3:
            _edit_script_schedule(path)


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

    log_action("VSCode Open", f"{ssh_alias}:{workspace}")
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
        result = _ssh("cat /proc/mdstat 2>/dev/null")
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
        last_check = _ssh(
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
                log_action("Parity Check Start", "non-correcting")
                r = _ssh("mdcmd check noCorrect 2>/dev/null || echo 'mdcmd not available'")
                if r.returncode == 0:
                    success("Parity check started (non-correcting).")
                else:
                    error(f"Failed: {r.stderr.strip()}")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Start correcting check":
            if confirm("Start a CORRECTING parity check?", default_yes=False):
                log_action("Parity Check Start", "correcting")
                r = _ssh("mdcmd check 2>/dev/null || echo 'mdcmd not available'")
                if r.returncode == 0:
                    success("Parity check started (correcting).")
                else:
                    error(f"Failed: {r.stderr.strip()}")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Cancel parity check":
            if confirm("Cancel the running parity check?", default_yes=False):
                log_action("Parity Check Cancel", "")
                r = _ssh("mdcmd nocheck 2>/dev/null")
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
        result = _ssh(
            "find /tmp/notifications -name '*.notify' -type f 2>/dev/null | sort -r | head -50"
        )
        if result.returncode != 0 or not result.stdout.strip():
            info("No notifications found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        notifications = []

        for nf in files:
            content = _ssh(f"cat '{nf}' 2>/dev/null")
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
                log_action("Notifications Dismiss All", f"{len(notifications)} notifications")
                for n in notifications:
                    _ssh(f"rm -f '{n['file']}'")
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
        _ssh(f"rm -f '{notif['file']}'")
        success("Notification dismissed.")
