"""Docker Host plugin — manage generic Linux servers with Docker."""

import os
import subprocess
import time

from homelab.config import CFG, save_config
from homelab.modules.auditlog import log_action
from homelab.plugins import Plugin
from homelab.modules.transport import ssh_run
from homelab.modules.files import (
    manage_files, upload_local, download_from_server, fetch_and_transfer,
)
from homelab.ui import (
    C, pick_option, pick_multi, confirm, prompt_text, scrollable_list,
    info, success, error, warn, bar_chart, sparkline,
)


class DockerHostPlugin(Plugin):
    name = "Docker Servers"
    key = "dockerhost"

    def is_configured(self):
        return True  # Always available so users can add servers

    def get_config_fields(self):
        return []

    def get_header_stats(self):
        return None

    def get_menu_items(self):
        items = []
        for server in CFG.get("docker_servers", []):
            name = server.get("name", "?")
            host = server.get("host", "?")
            label = f"{name:<21}— {host}"
            items.append((label, lambda s=server: _server_menu(s)))
        return items

    def get_actions(self):
        actions = {
            "Docker Servers": ("docker_servers_menu", docker_servers_menu),
        }
        for server in CFG.get("docker_servers", []):
            name = server.get("name", "?")
            actions[f"Server: {name}"] = (
                f"docker_server_{name}",
                lambda s=server: _server_menu(s),
            )
            actions[f"{name}: System Tools"] = (
                f"docker_systools_{name}",
                lambda s=server: _server_system_tools(s),
            )
            actions[f"{name}: System Stats"] = (
                f"docker_sysstats_{name}",
                lambda s=server: _server_system_stats(s),
            )
            actions[f"{name}: Containers"] = (
                f"docker_containers_{name}",
                lambda s=server: _server_containers_hub(s),
            )
            actions[f"{name}: SSH Shell"] = (
                f"docker_ssh_{name}",
                lambda s=server: _server_ssh_shell(s),
            )
            actions[f"{name}: Port Map"] = (
                f"docker_portmap_{name}",
                lambda s=server: _run_tool(s, "portmap"),
            )
            actions[f"{name}: Systemd Services"] = (
                f"docker_services_{name}",
                lambda s=server: _run_tool(s, "services"),
            )
            actions[f"{name}: Process Explorer"] = (
                f"docker_processes_{name}",
                lambda s=server: _run_tool(s, "processes"),
            )
            actions[f"{name}: Firewall Rules"] = (
                f"docker_firewall_{name}",
                lambda s=server: _run_tool(s, "firewall"),
            )
            actions[f"{name}: Mount Monitor"] = (
                f"docker_mounts_{name}",
                lambda s=server: _run_tool(s, "mounts"),
            )
            actions[f"{name}: Crontab"] = (
                f"docker_crontab_{name}",
                lambda s=server: _server_crontab(s),
            )
            actions[f"{name}: Docker Volumes"] = (
                f"docker_volumes_{name}",
                lambda s=server: _run_tool(s, "volumes"),
            )
        return actions


# ─── Server Management ────────────────────────────────────────────────────

def docker_servers_menu():
    while True:
        servers = CFG.get("docker_servers", [])
        choices = []
        for s in servers:
            name = s.get("name", "?")
            host = s.get("host", "?")
            port = s.get("port", "")
            port_str = f" :{port}" if port else ""
            choices.append(f"{name:<20} {C.DIM}{host}{port_str}{C.RESET}")

        choices.extend([
            "───────────────",
            "+ Add Server",
            "- Remove Server",
            "← Back",
        ])

        idx = pick_option("Docker Servers:", choices)
        n_servers = len(servers)

        if idx == n_servers + 3:  # Back
            return
        elif idx == n_servers:  # Separator
            continue
        elif idx == n_servers + 1:  # Add
            _add_server()
        elif idx == n_servers + 2:  # Remove
            _remove_server()
        elif idx < n_servers:
            _server_menu(servers[idx])


def _add_server():
    name = prompt_text("Display name (e.g. NAS, Media Box):")
    if not name:
        return
    host = prompt_text("SSH host (e.g. root@10.0.0.5):")
    if not host:
        return
    base_path = prompt_text("Base path for file browser (default: /):", default="/")
    port = prompt_text("SSH port (leave blank for 22):")

    # Test connection
    info(f"Testing connection to {host}...")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    if port:
        cmd.extend(["-p", port])
    cmd.extend([host, "echo ok"])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            error(f"Connection failed: {result.stderr.strip()[:100]}")
            if not confirm("Save anyway?", default_yes=False):
                return
    except subprocess.TimeoutExpired:
        error("Connection timed out.")
        if not confirm("Save anyway?", default_yes=False):
            return

    entry = {"name": name, "host": host, "base_path": base_path or "/"}
    if port:
        entry["port"] = port

    servers = CFG.get("docker_servers", [])
    servers.append(entry)
    CFG["docker_servers"] = servers
    save_config(CFG)
    log_action("Docker Add Server", f"{name} ({host})")
    success(f"Added server: {name} ({host})")


def _remove_server():
    servers = CFG.get("docker_servers", [])
    if not servers:
        warn("No servers to remove.")
        return
    choices = [f"{s['name']} ({s['host']})" for s in servers]
    choices.append("Cancel")
    idx = pick_option("Remove which server?", choices)
    if idx >= len(servers):
        return
    removed = servers.pop(idx)
    CFG["docker_servers"] = servers
    save_config(CFG)
    log_action("Docker Remove Server", f"{removed['name']} ({removed.get('host', '?')})")
    success(f"Removed: {removed['name']}")


def _ssh_host_arg(server):
    """Build SSH host string, handling port if set."""
    return server.get("host", "")


def _ssh_cmd(server, command):
    """Run an SSH command on a generic server."""
    host = _ssh_host_arg(server)
    port = server.get("port", "")
    return ssh_run(command, host=host, port=port or None)


def _ssh_tty(server, command):
    """Run an interactive SSH command (TTY) on a generic server."""
    host = _ssh_host_arg(server)
    port = server.get("port", "")
    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", port])
    cmd.extend([host, command])
    subprocess.run(cmd)


# ─── Per-Server Menu ──────────────────────────────────────────────────────

def _server_menu(server):
    name = server.get("name", "?")
    host = server.get("host", "?")
    while True:
        idx = pick_option(f"{name} ({host}):", [
            "Files             — browse, upload, download, transfer",
            "Containers        — manage, compose, bulk ops, stats, logs",
            "Docker Resources  — images, networks, volumes, prune",
            "Container Updates — check for image updates",
            "───────────────",
            "System Stats      — CPU, RAM, uptime, disk usage",
            "System Tools      — ports, services, processes, firewall, mounts",
            "SSH Shell         — open terminal on this server",
            "───────────────",
            "★ Add to Favorites — pin an action to the main menu",
            "← Back",
        ])
        if idx == 10:
            return
        elif idx in (4, 8):
            continue
        elif idx == 0:
            _server_files_menu(server)
        elif idx == 1:
            _server_containers_hub(server)
        elif idx == 2:
            _server_docker_resources(server)
        elif idx == 3:
            from homelab.modules.containerupdates import check_container_updates
            check_container_updates(host=_ssh_host_arg(server))
        elif idx == 5:
            _server_system_stats(server)
        elif idx == 6:
            _server_system_tools(server)
        elif idx == 7:
            _server_ssh_shell(server)
        elif idx == 9:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(DockerHostPlugin())


def _server_containers_hub(server):
    """Hub view: container list with summary header and action items at bottom."""
    while True:
        result = _ssh_cmd(
            server,
            "docker ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}' 2>/dev/null"
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No Docker containers found (is Docker installed?).")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        lines = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
        containers = []
        choices = []
        running = 0
        stopped = 0
        for line in lines:
            parts = line.split("\t")
            cname = parts[0]
            status = parts[1] if len(parts) > 1 else "?"
            image = parts[2] if len(parts) > 2 else "?"
            containers.append({"name": cname, "status": status, "image": image})
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
            choices.append(f"{icon} {cname:<25} {C.DIM}{image}{C.RESET}")

        header = (
            f"\n  {C.ACCENT}{C.BOLD}Containers{C.RESET}"
            f"  {len(containers)} total  |  {C.GREEN}{running} running{C.RESET}"
            f"  {C.RED}{stopped} stopped{C.RESET}\n")

        # Action items at bottom
        choices.append("───────────────")
        action_start = len(containers) + 1
        choices.append("Docker Compose    — manage compose projects")
        choices.append("Bulk Operations   — select and manage multiple containers")
        choices.append("Docker Stats      — CPU, RAM per container")
        choices.append("Resource Graphs   — CPU/memory trends per container")
        choices.append("Live Logs         — tail container logs")
        choices.append("← Back")

        idx = pick_option("Containers:", choices, header=header)

        if idx == action_start + 5:  # Back
            return
        elif idx == len(containers):  # separator
            continue
        elif idx == action_start:
            _server_docker_compose(server)
        elif idx == action_start + 1:
            _server_bulk_ops(server)
        elif idx == action_start + 2:
            _server_docker_stats(server)
        elif idx == action_start + 3:
            _server_resource_graph(server)
        elif idx == action_start + 4:
            _server_live_logs(server)
        elif idx < len(containers):
            _server_container_actions(server, containers[idx])


def _server_docker_resources(server):
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
            _server_docker_images(server)
        elif idx == 1:
            _server_docker_networks(server)
        elif idx == 2:
            from homelab.modules.volumes import docker_volumes
            docker_volumes(host=_ssh_host_arg(server), port=server.get("port", "") or None, label=server.get("name", "?"))
        elif idx == 3:
            _server_system_prune(server)


# ─── Files ────────────────────────────────────────────────────────────────

def _server_files_menu(server):
    """Submenu for file operations on a server."""
    from homelab.config import local_hostname
    host = _ssh_host_arg(server)
    port = server.get("port", "") or None
    base_path = server.get("base_path", "/")
    hostname = local_hostname()
    while True:
        idx = pick_option("Files:", [
            "File Manager         — browse, search, manage files",
            f"Download from Server — pull files to {hostname}",
            "Transfer to Server   — upload local files or download from web",
            "← Back",
        ])
        if idx == 3:
            return
        elif idx == 0:
            manage_files(host=host, base_path=base_path, port=port)
        elif idx == 1:
            download_from_server(host=host, base_path=base_path, port=port)
        elif idx == 2:
            _server_transfer_menu(server)


def _server_transfer_menu(server):
    """Sub-menu for transferring files to the server."""
    host = _ssh_host_arg(server)
    port = server.get("port", "") or None
    base_path = server.get("base_path", "/")
    idx = pick_option("Transfer to Server:", [
        "Upload local files   — send files from this machine",
        "Download from web    — wget, curl, yt-dlp, git clone",
        "← Back",
    ])
    if idx == 2:
        return
    elif idx == 0:
        upload_local(host=host, base_path=base_path, port=port)
    elif idx == 1:
        fetch_and_transfer(host=host, base_path=base_path)


# ─── System Stats ─────────────────────────────────────────────────────────

def _server_system_stats(server):
    cmd = (
        "hostname 2>/dev/null;"
        "echo '---SEP---';"
        "uptime -p 2>/dev/null || uptime;"
        "echo '---SEP---';"
        "free -b 2>/dev/null;"
        "echo '---SEP---';"
        "df -B1 / 2>/dev/null | tail -1;"
        "echo '---SEP---';"
        "nproc 2>/dev/null;"
        "echo '---SEP---';"
        "cat /proc/loadavg 2>/dev/null;"
        "echo '---SEP---';"
        "cat /etc/os-release 2>/dev/null | head -2"
    )
    result = _ssh_cmd(server, cmd)
    if result.returncode != 0:
        error(f"Failed to get system stats: {result.stderr.strip()[:100]}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    parts = result.stdout.split("---SEP---")
    if len(parts) < 7:
        error("Unexpected output from server.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    hostname = parts[0].strip() or "?"
    uptime_str = parts[1].strip().replace("up ", "")
    free_output = parts[2].strip()
    df_output = parts[3].strip()
    nproc = parts[4].strip() or "?"
    loadavg = parts[5].strip()
    os_release = parts[6].strip()

    # Parse OS name
    os_name = "?"
    for line in os_release.split("\n"):
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
            break

    # Parse memory
    mem_total = mem_used = 0
    for line in free_output.split("\n"):
        if line.startswith("Mem:"):
            mem_parts = line.split()
            if len(mem_parts) >= 3:
                try:
                    mem_total = int(mem_parts[1])
                    mem_used = int(mem_parts[2])
                except ValueError:
                    pass
            break

    # Parse disk
    disk_total = disk_used = 0
    df_parts = df_output.split()
    if len(df_parts) >= 4:
        try:
            disk_total = int(df_parts[1])
            disk_used = int(df_parts[2])
        except ValueError:
            pass

    # Parse load average
    load_parts = loadavg.split()
    load_str = " ".join(load_parts[:3]) if len(load_parts) >= 3 else loadavg

    name = server.get("name", "?")
    print(f"\n  {C.BOLD}{name} — System Stats{C.RESET}\n")
    print(f"  Hostname:  {hostname}")
    print(f"  OS:        {os_name}")
    print(f"  Uptime:    {uptime_str}")
    print(f"  CPUs:      {nproc}")
    print(f"  Load:      {load_str}")
    print()
    if mem_total > 0:
        mem_gb_used = mem_used / (1024 ** 3)
        mem_gb_total = mem_total / (1024 ** 3)
        print(f"  Memory:    {bar_chart(mem_used, mem_total, width=25)}  "
              f"{mem_gb_used:.1f} / {mem_gb_total:.1f} GB")
    if disk_total > 0:
        disk_gb_used = disk_used / (1024 ** 3)
        disk_gb_total = disk_total / (1024 ** 3)
        print(f"  Disk /:    {bar_chart(disk_used, disk_total, width=25)}  "
              f"{disk_gb_used:.1f} / {disk_gb_total:.1f} GB")
    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Docker Containers ───────────────────────────────────────────────────

def _server_container_actions(server, container):
    cname = container["name"]
    server_name = server.get("name", "?")
    status = container["status"]
    is_running = "Up" in status

    if is_running:
        choices = [
            "Stop", "Restart", "Shell (exec bash)",
            "Logs (follow)", "Logs (last 100)", "Inspect",
            "Update (pull + restart)", "← Back",
        ]
    else:
        choices = ["Start", "Logs (last 100)", "Inspect", "Remove", "← Back"]

    print(f"\n  {C.BOLD}Container: {cname}{C.RESET}")
    print(f"  Image:  {container['image']}")
    print(f"  Status: {status}")

    aidx = pick_option(f"{cname}:", choices)
    action = choices[aidx]

    if action == "← Back":
        return
    elif action == "Start":
        _ssh_cmd(server, f"docker start {cname}")
        log_action("Docker Start", f"{cname} on {server_name}")
        success(f"Started {cname}")
    elif action == "Stop":
        _ssh_cmd(server, f"docker stop {cname}")
        log_action("Docker Stop", f"{cname} on {server_name}")
        success(f"Stopped {cname}")
    elif action == "Restart":
        _ssh_cmd(server, f"docker restart {cname}")
        log_action("Docker Restart", f"{cname} on {server_name}")
        success(f"Restarted {cname}")
    elif action == "Shell (exec bash)":
        _ssh_tty(server, f"docker exec -it {cname} bash || docker exec -it {cname} sh")
    elif action == "Logs (follow)":
        _ssh_tty(server, f"docker logs -f --tail 100 {cname}")
    elif action == "Logs (last 100)":
        result = _ssh_cmd(server, f"docker logs --tail 100 {cname} 2>&1")
        if result.stdout.strip():
            log_lines = result.stdout.strip().split("\n")
            scrollable_list(f"Logs: {cname}", log_lines)
        else:
            warn("No logs available.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif action == "Inspect":
        _server_container_inspect(server, cname)
    elif action == "Update (pull + restart)":
        _server_container_update(server, cname)
    elif action == "Remove":
        if confirm(f"Remove container {cname}? This cannot be undone.", default_yes=False):
            _ssh_cmd(server, f"docker rm {cname}")
            log_action("Docker Remove", f"{cname} on {server_name}")
            success(f"Removed {cname}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_container_inspect(server, cname):
    """Show detailed info for a container — ports, mounts, networks, env."""
    result = _ssh_cmd(
        server,
        f"docker inspect {cname} --format '"
        "Image: {{{{.Config.Image}}}}\\n"
        "Created: {{{{.Created}}}}\\n"
        "RestartPolicy: {{{{.HostConfig.RestartPolicy.Name}}}}\\n"
        "Health: {{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{else}}}}N/A{{{{end}}}}\\n"
        "' 2>/dev/null"
    )
    print(f"\n  {C.BOLD}Container: {cname}{C.RESET}\n")
    if result.returncode == 0:
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                print(f"  {line.strip()}")

    # Ports
    ports_r = _ssh_cmd(
        server,
        f"docker inspect {cname} --format '{{{{range $p, $conf := .NetworkSettings.Ports}}}}"
        f"{{{{$p}}}} -> {{{{range $conf}}}}{{{{.HostPort}}}}{{{{end}}}}\\n{{{{end}}}}' 2>/dev/null"
    )
    if ports_r.returncode == 0 and ports_r.stdout.strip():
        print(f"\n  {C.BOLD}Ports:{C.RESET}")
        for line in ports_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Volumes/Mounts
    mounts_r = _ssh_cmd(
        server,
        f"docker inspect {cname} --format '{{{{range .Mounts}}}}"
        f"{{{{.Source}}}} -> {{{{.Destination}}}} ({{{{.Type}}}})\\n{{{{end}}}}' 2>/dev/null"
    )
    if mounts_r.returncode == 0 and mounts_r.stdout.strip():
        print(f"\n  {C.BOLD}Mounts:{C.RESET}")
        for line in mounts_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Networks
    nets_r = _ssh_cmd(
        server,
        f"docker inspect {cname} --format '{{{{range $k, $v := .NetworkSettings.Networks}}}}"
        f"{{{{$k}}}} ({{{{$v.IPAddress}}}})\\n{{{{end}}}}' 2>/dev/null"
    )
    if nets_r.returncode == 0 and nets_r.stdout.strip():
        print(f"\n  {C.BOLD}Networks:{C.RESET}")
        for line in nets_r.stdout.strip().split("\n"):
            if line.strip():
                print(f"    {line.strip()}")

    # Environment variables (filtered for secrets)
    env_r = _ssh_cmd(
        server,
        f"docker inspect {cname} --format '{{{{range .Config.Env}}}}{{{{.}}}}\\n{{{{end}}}}' 2>/dev/null"
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


def _server_container_update(server, cname):
    """Pull latest image and restart a container."""
    server_name = server.get("name", "?")
    img_r = _ssh_cmd(
        server,
        f"docker inspect {cname} --format '{{{{.Config.Image}}}}' 2>/dev/null"
    )
    if img_r.returncode != 0 or not img_r.stdout.strip():
        error("Could not determine container image.")
        return

    image = img_r.stdout.strip()
    print(f"\n  {C.BOLD}Update: {cname}{C.RESET}")
    print(f"  {C.BOLD}Image:{C.RESET}  {image}\n")

    if not confirm(f"Pull latest {image} and restart {cname}?"):
        return

    log_action("Docker Update", f"{cname} ({image}) on {server_name}")
    info(f"Pulling {image}...")
    _ssh_tty(server, f"docker pull {image}")

    info(f"Restarting {cname}...")
    _ssh_cmd(server, f"docker stop {cname}")
    _ssh_cmd(server, f"docker start {cname}")
    success(f"Updated and restarted: {cname}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Docker Compose ───────────────────────────────────────────────────────

def _server_docker_compose(server):
    while True:
        # Find compose files
        result = _ssh_cmd(
            server,
            "find /opt /home /root /srv /docker -maxdepth 4 "
            "\\( -name docker-compose.yml -o -name docker-compose.yaml -o -name compose.yml -o -name compose.yaml \\) "
            "2>/dev/null | head -30"
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No Docker Compose files found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        projects = []
        choices = []
        for f in files:
            project_dir = os.path.dirname(f)
            project_name = os.path.basename(project_dir)
            projects.append({"name": project_name, "path": project_dir, "file": f})
            choices.append(f"{project_name:<25} {C.DIM}{project_dir}{C.RESET}")

        choices.append("───────────────")
        choices.append("Git                      — pull, commit, push")
        choices.append("← Back")
        idx = pick_option("Compose Projects:", choices)

        if idx == len(projects) + 2:  # Back
            return
        elif idx == len(projects):  # Separator
            continue
        elif idx == len(projects) + 1:  # Git
            # Use common parent of all compose projects
            parents = list({p["path"] for p in projects})
            common = os.path.commonpath(parents) if len(parents) > 1 else parents[0]
            _server_git_menu(server, common, os.path.basename(common))
        else:
            _server_compose_actions(server, projects[idx])


def _server_git_menu(server, path, label):
    """Git operations for a remote directory."""
    check = _ssh_cmd(server, f"cd '{path}' && git rev-parse --is-inside-work-tree 2>/dev/null")
    if check.returncode != 0 or "true" not in check.stdout:
        warn("Not a git repository.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        status = _ssh_cmd(server, f"cd '{path}' && git status --short 2>/dev/null")
        branch = _ssh_cmd(server, f"cd '{path}' && git branch --show-current 2>/dev/null")
        branch_name = branch.stdout.strip() if branch.returncode == 0 else "?"

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
            # Fix permissions if needed (common with Docker-managed repos)
            check = _ssh_cmd(server, f"test -w '{path}/.git/objects' && echo 'ok'")
            if "ok" not in check.stdout:
                info("Fixing .git permissions...")
                _ssh_cmd(server, f"sudo chown -R $(whoami):$(id -gn) '{path}/.git'")
            info("Pulling...")
            _ssh_tty(server, f"cd '{path}' && git pull")
            log_action("Git Pull", f"{label} on {server.get('name', '?')}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx == 1:
            _server_git_commit_push(server, path, label)
        elif idx == 2:
            r = _ssh_cmd(server, f"cd '{path}' && git log --oneline -10 2>/dev/null")
            lines = r.stdout.strip().split("\n") if r.stdout.strip() else ["(no commits)"]
            scrollable_list(f"Git Log: {label}", lines)


def _server_git_commit_push(server, path, label):
    """Stage all changes, commit with a message, and push."""
    # Check for changes
    status = _ssh_cmd(server, f"cd '{path}' && git status --short 2>/dev/null")
    if not status.stdout.strip():
        warn("No changes to commit.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Ensure git user.name and user.email are set
    name_r = _ssh_cmd(server, f"cd '{path}' && git config user.name 2>/dev/null")
    email_r = _ssh_cmd(server, f"cd '{path}' && git config user.email 2>/dev/null")
    if not name_r.stdout.strip():
        git_name = prompt_text("Git user.name (not set on remote):")
        if not git_name:
            return
        _ssh_cmd(server, f"cd '{path}' && git config user.name '{git_name}'")
    if not email_r.stdout.strip():
        git_email = prompt_text("Git user.email (not set on remote):")
        if not git_email:
            return
        _ssh_cmd(server, f"cd '{path}' && git config user.email '{git_email}'")

    msg = prompt_text("Commit message:")
    if not msg:
        return

    safe_msg = msg.replace("'", "'\\''")
    # Fix permissions if needed
    check = _ssh_cmd(server, f"test -w '{path}/.git/objects' && echo 'ok'")
    if "ok" not in check.stdout:
        info("Fixing .git permissions...")
        _ssh_cmd(server, f"sudo chown -R $(whoami):$(id -gn) '{path}/.git'")
    info("Committing and pushing...")
    _ssh_tty(server, f"cd '{path}' && git add -A && git commit -m '{safe_msg}' && git push")
    log_action("Git Commit & Push", f"{label} on {server.get('name', '?')}: {msg[:60]}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_compose_actions(server, project):
    name = project["name"]
    path = project["path"]
    server_name = server.get("name", "?")

    while True:
        hdr = f"\n  {C.BOLD}Project: {name}{C.RESET}\n"

        # Fetch containers for inline display
        ct_result = _ssh_cmd(
            server,
            f"cd '{path}' && docker compose ps --format "
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
            "Up (deploy)",
            "Down (stop)",
            "Pull & Up (update)      — pull latest images and redeploy",
            "Logs (follow)",
            "Restart service         — restart a single service",
            "───────────────",
            "Edit compose file",
            "Edit .env file",
            "Validate                — check compose syntax",
            "← Back",
        ])
        aidx = pick_option("", choices, header=hdr)

        if aidx < len(containers):
            _server_compose_container_actions(server, name, containers[aidx])
            continue
        elif aidx == len(containers) or aidx == action_start + 5:
            continue  # separators

        choice = choices[aidx]
        if choice == "← Back":
            return
        elif choice == "Edit compose file":
            _ssh_tty(server, f"nano '{project['file']}'")
        elif choice == "Edit .env file":
            env_file = f"{path}/.env"
            _ssh_cmd(server, f"touch '{env_file}'")
            _ssh_tty(server, f"nano '{env_file}'")
        elif "Validate" in choice:
            info("Validating compose file...")
            r = _ssh_cmd(server, f"cd '{path}' && docker compose config 2>&1")
            if r.returncode == 0:
                success("Compose file is valid.")
            else:
                error("Validation failed:")
                print(f"\n{r.stdout.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Restart service" in choice:
            _server_compose_restart_service(server, path, name)
        elif choice == "Up (deploy)":
            log_action("Compose Up", f"{name} on {server_name}")
            info("Deploying...")
            _ssh_tty(server, f"cd '{path}' && docker compose up -d")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Down (stop)":
            if confirm(f"Stop {name}?"):
                log_action("Compose Down", f"{name} on {server_name}")
                info("Stopping...")
                _ssh_tty(server, f"cd '{path}' && docker compose down")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Pull & Up" in choice:
            log_action("Compose Update", f"{name} on {server_name}")
            info("Pulling and deploying...")
            _ssh_tty(server, f"cd '{path}' && docker compose pull && docker compose up -d")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif choice == "Logs (follow)":
            _server_compose_logs(server, path, name)
        elif "Status" in choice:
            result = _ssh_cmd(server, f"cd '{path}' && docker compose ps 2>&1")
            if result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                scrollable_list(f"Status: {name}", lines)
            else:
                warn("No output.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_compose_container_actions(server, project_name, container):
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
            server_name = server.get("name", "?")
            log_action("Compose Container Stop", f"{project_name}/{cname} on {server_name}")
            _ssh_cmd(server, f"docker stop {cname}")
            success(f"Stopped: {cname}")
            return
        elif action == "Start":
            server_name = server.get("name", "?")
            log_action("Compose Container Start", f"{project_name}/{cname} on {server_name}")
            _ssh_cmd(server, f"docker start {cname}")
            success(f"Started: {cname}")
            return
        elif action == "Restart":
            server_name = server.get("name", "?")
            log_action("Compose Container Restart", f"{project_name}/{cname} on {server_name}")
            _ssh_cmd(server, f"docker restart {cname}")
            success(f"Restarted: {cname}")
            return
        elif action == "Logs (follow)":
            _ssh_tty(server, f"docker logs -f --tail 50 {cname}")
        elif action == "Logs (last 100 lines)":
            r = _ssh_cmd(server, f"docker logs --tail 100 {cname} 2>&1")
            if r.stdout.strip():
                lines = r.stdout.strip().split("\n")
                scrollable_list(f"Logs: {cname}", lines)
            else:
                warn("No log output.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif action == "Shell":
            _ssh_tty(server, f"docker exec -it {cname} /bin/sh")
        elif action == "Inspect":
            r = _ssh_cmd(
                server,
                f"docker inspect {cname} --format '"
                f"ID: {{{{.Id}}}}\\n"
                f"Created: {{{{.Created}}}}\\n"
                f"RestartCount: {{{{.RestartCount}}}}\\n"
                f"Platform: {{{{.Platform}}}}\\n"
                f"' 2>/dev/null"
            )
            if r.returncode == 0 and r.stdout.strip():
                print(f"\n  {C.BOLD}{cname}{C.RESET}\n")
                for line in r.stdout.strip().split("\n"):
                    if line.strip():
                        print(f"  {line.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_compose_restart_service(server, path, project_name):
    """Restart a single service within a compose project."""
    result = _ssh_cmd(
        server,
        f"cd '{path}' && docker compose config --services 2>/dev/null"
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
    server_name = server.get("name", "?")
    log_action("Compose Restart Service", f"{project_name}/{svc} on {server_name}")
    info(f"Restarting {svc}...")
    _ssh_tty(server, f"cd '{path}' && docker compose restart {svc}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_compose_logs(server, path, project_name):
    """Follow logs for all or a specific service in a compose project."""
    result = _ssh_cmd(
        server,
        f"cd '{path}' && docker compose config --services 2>/dev/null"
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
        _ssh_tty(server, f"cd '{path}' && docker compose logs -f --tail 50")
    elif "View last 100" in choice and "all" in choice:
        r = _ssh_cmd(server, f"cd '{path}' && docker compose logs --tail 100 2>&1")
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
            _ssh_tty(server, f"cd '{path}' && docker compose logs -f --tail 50 {services[sidx]}")
    elif "View service logs" in choice:
        svc_choices = services + ["← Back"]
        sidx = pick_option("View logs for:", svc_choices)
        if sidx < len(services):
            r = _ssh_cmd(server, f"cd '{path}' && docker compose logs --tail 100 {services[sidx]} 2>&1")
            if r.stdout.strip():
                lines = r.stdout.strip().split("\n")
                scrollable_list(f"Logs: {project_name}/{services[sidx]}", lines)
            else:
                warn("No log output.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Docker Stats ─────────────────────────────────────────────────────────

def _server_docker_stats(server):
    result = _ssh_cmd(
        server,
        "docker stats --no-stream --format "
        "'{{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}\\t{{.MemPerc}}\\t{{.NetIO}}' 2>/dev/null"
    )
    if result.returncode != 0 or not result.stdout.strip():
        error("Failed to get Docker stats.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    name = server.get("name", "?")
    print(f"\n  {C.BOLD}{name} — Docker Resource Usage{C.RESET}\n")
    print(f"  {C.DIM}{'Container':<28} {'CPU':>7}  {'Memory':>22}  {'Net I/O':>20}{C.RESET}")
    print(f"  {'─' * 82}")

    lines = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
    for line in sorted(lines):
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        cname, cpu, mem, mem_pct, net = parts
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
        print(f"  {cname:<28} {cpu_color}{cpu:>7}{C.RESET}  {mem:>22}  {net:>20}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Resource Graphs ─────────────────────────────────────────────────────

def _server_resource_graph(server):
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
        result = _ssh_cmd(
            server,
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
            cname, cpu_str, mem_str, mem_usage = parts
            if cname not in history:
                history[cname] = {"cpu": [], "mem": [], "mem_usage": ""}
            try:
                history[cname]["cpu"].append(float(cpu_str.rstrip("%")))
            except ValueError:
                history[cname]["cpu"].append(0)
            try:
                history[cname]["mem"].append(float(mem_str.rstrip("%")))
            except ValueError:
                history[cname]["mem"].append(0)
            history[cname]["mem_usage"] = mem_usage

    print("\r" + " " * 40 + "\r", end="")  # Clear progress line

    if not history:
        error("Failed to collect Docker stats.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    # Dynamic name column width (cap at 28, truncate with …)
    max_name = min(28, max(len(n) for n in history.keys()))
    max_name = max(max_name, 9)  # At least "Container" width
    total_w = max_name + 2 + samples + 7 + 2 + samples + 2 + 20

    sname = server.get("name", "?")
    print(f"  {C.BOLD}{sname} — Container Resource Trends{C.RESET}  {C.DIM}({samples} samples, {interval}s interval){C.RESET}\n")
    print(f"  {C.DIM}{'Container':<{max_name}}  {'CPU':^{samples + 7}}  {'Memory':^{samples + 20}}{C.RESET}")
    print(f"  {'─' * total_w}")

    for cname in sorted(history.keys()):
        data = history[cname]
        cpu_vals = data["cpu"]
        mem_vals = data["mem"]
        last_cpu = cpu_vals[-1] if cpu_vals else 0
        mem_usage = data["mem_usage"]

        cpu_spark = sparkline(cpu_vals, width=samples)
        mem_spark = sparkline(mem_vals, width=samples)

        # Truncate long names
        display = cname if len(cname) <= max_name else cname[:max_name - 1] + "…"

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


# ─── Docker Images ────────────────────────────────────────────────────────

def _server_docker_images(server):
    while True:
        result = _ssh_cmd(
            server,
            "docker images --format '{{.Repository}}:{{.Tag}}\\t{{.Size}}\\t{{.ID}}\\t{{.CreatedSince}}' 2>/dev/null"
        )
        if result.returncode != 0 or not result.stdout.strip():
            warn("No Docker images found.")
            return

        lines = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
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
        dangling = _ssh_cmd(server, "docker images -f dangling=true -q 2>/dev/null")
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
                log_action("Docker Prune", f"dangling images on {server.get('name', '?')}")
                info("Pruning dangling images...")
                _ssh_cmd(server, "docker image prune -f")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif "Prune all" in choice:
            if confirm("Remove ALL unused images? This cannot be undone.", default_yes=False):
                log_action("Docker Prune", f"all unused images on {server.get('name', '?')}")
                info("Pruning all unused images...")
                _ssh_cmd(server, "docker image prune -a -f")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx < len(images):
            img = images[idx]
            action = pick_option(
                f"Image: {img['repo']} ({img['size']})",
                ["Remove image", "Cancel"],
            )
            if action == 0:
                if confirm(f"Remove {img['repo']}?", default_yes=False):
                    r = _ssh_cmd(server, f"docker rmi {img['id']}")
                    if r.returncode == 0:
                        log_action("Docker Image Remove", f"{img['repo']} on {server.get('name', '?')}")
                        success(f"Removed: {img['repo']}")
                    else:
                        error(f"Failed (image may be in use): {r.stderr.strip()[:80]}")
                    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── System Prune ────────────────────────────────────────────────────────

def _server_docker_networks(server):
    """Manage Docker networks on a server."""
    while True:
        result = _ssh_cmd(
            server,
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
            _server_network_create(server)
        elif idx < len(networks):
            _server_network_detail(server, networks[idx])


def _server_network_detail(server, net):
    """Show detail and actions for a Docker network."""
    result = _ssh_cmd(server, f"docker network inspect {net['name']} 2>/dev/null")
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

    builtin = {"bridge", "host", "none"}
    actions = []
    if name not in builtin:
        actions.append("Remove Network")
    actions.append("← Back")
    aidx = pick_option(f"Network: {name}", actions)
    if actions[aidx] == "Remove Network":
        server_name = server.get("name", "?")
        if confirm(f"Remove network {name}?", default_yes=False):
            log_action("Docker Network Remove", f"{name} on {server_name}")
            r = _ssh_cmd(server, f"docker network rm {name}")
            if r.returncode == 0:
                success(f"Removed: {name}")
            else:
                error(f"Failed: {r.stderr.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_network_create(server):
    """Create a new Docker network on a server."""
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
    server_name = server.get("name", "?")
    if confirm(f"Create network: {name} ({driver})?"):
        log_action("Docker Network Create", f"{name} ({driver}) on {server_name}")
        r = _ssh_cmd(server, cmd)
        if r.returncode == 0:
            success(f"Created network: {name}")
        else:
            error(f"Failed: {r.stderr.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_docker_volumes(server):
    """Manage Docker volumes on a server."""
    while True:
        result = _ssh_cmd(
            server,
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
            _server_volume_prune(server)
        elif idx < len(volumes):
            _server_volume_detail(server, volumes[idx])


def _server_volume_detail(server, vol):
    """Show detail for a Docker volume."""
    result = _ssh_cmd(server, f"docker volume inspect {vol['name']} 2>/dev/null")
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

    du_result = _ssh_cmd(server, f"du -sh {mountpoint} 2>/dev/null")
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
        server_name = server.get("name", "?")
        if confirm(f"Remove volume {name}? Data will be lost.", default_yes=False):
            log_action("Docker Volume Remove", f"{name} on {server_name}")
            r = _ssh_cmd(server, f"docker volume rm {name}")
            if r.returncode == 0:
                success(f"Removed: {name}")
            else:
                error(f"Failed: {r.stderr.strip()}")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_volume_prune(server):
    """Prune unused Docker volumes on a server."""
    if not confirm("Remove all unused volumes? This cannot be undone.", default_yes=False):
        return
    server_name = server.get("name", "?")
    log_action("Docker Volume Prune", f"unused volumes on {server_name}")
    r = _ssh_cmd(server, "docker volume prune -f 2>/dev/null")
    if r.returncode == 0:
        success("Pruned unused volumes.")
        if r.stdout.strip():
            print(f"  {C.DIM}{r.stdout.strip()}{C.RESET}")
    else:
        error(f"Failed: {r.stderr.strip()}")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_system_prune(server):
    """Clean up unused Docker resources."""
    server_name = server.get("name", "?")
    result = _ssh_cmd(server, "docker system df 2>/dev/null")
    if result.returncode == 0 and result.stdout.strip():
        print(f"\n  {C.BOLD}{server_name} — Docker Disk Usage{C.RESET}\n")
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
            log_action("Docker System Prune", f"basic on {server_name}")
            info("Pruning...")
            _ssh_cmd(server, "docker system prune -f")
            print()
            success("Basic prune complete.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif idx == 1:
        if confirm("Run FULL prune? This removes ALL unused images and volumes.", default_yes=False):
            log_action("Docker System Prune", f"full on {server_name}")
            info("Pruning everything...")
            _ssh_cmd(server, "docker system prune -a --volumes -f")
            print()
            success("Full prune complete.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Bulk Operations ─────────────────────────────────────────────────────

def _server_bulk_ops(server):
    """Bulk operations on Docker containers."""
    server_name = server.get("name", "?")
    result = _ssh_cmd(
        server,
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
        cname = parts[0]
        status = parts[1] if len(parts) > 1 else ""
        if "Up" in status:
            running.append(cname)
        else:
            stopped.append(cname)

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
        return _server_bulk_ops(server)
    elif idx == 0 and running:
        choices = [f"{n:<25} [running]" for n in running]
        selected = pick_multi("Select containers to stop:", choices)
        if not selected:
            return
        names = [running[i] for i in selected]
        if confirm(f"Stop {len(names)} container(s)?"):
            log_action("Docker Bulk Stop", f"{len(names)} containers on {server_name}")
            for i, n in enumerate(names):
                info(f"Stopping {n}... ({i + 1}/{len(names)})")
                _ssh_cmd(server, f"docker stop {n}")
            success(f"Stopped {len(names)} containers.")
    elif idx == 1 and running:
        choices = [f"{n:<25} [running]" for n in running]
        selected = pick_multi("Select containers to restart:", choices)
        if not selected:
            return
        names = [running[i] for i in selected]
        if confirm(f"Restart {len(names)} container(s)?"):
            log_action("Docker Bulk Restart", f"{len(names)} containers on {server_name}")
            for i, n in enumerate(names):
                info(f"Restarting {n}... ({i + 1}/{len(names)})")
                _ssh_cmd(server, f"docker restart {n}")
            success(f"Restarted {len(names)} containers.")
    elif idx == 2 and stopped:
        choices = [f"{n:<25} [stopped]" for n in stopped]
        selected = pick_multi("Select containers to start:", choices)
        if not selected:
            return
        names = [stopped[i] for i in selected]
        if confirm(f"Start {len(names)} container(s)?"):
            log_action("Docker Bulk Start", f"{len(names)} containers on {server_name}")
            for i, n in enumerate(names):
                info(f"Starting {n}... ({i + 1}/{len(names)})")
                _ssh_cmd(server, f"docker start {n}")
            success(f"Started {len(names)} containers.")
    elif idx == 3 and running:
        _server_bulk_update(server, running)
    elif idx == 5 and running:
        if confirm(f"Stop {len(running)} running container(s)?"):
            log_action("Docker Bulk Stop", f"all {len(running)} running on {server_name}")
            for n in running:
                info(f"Stopping {n}...")
                _ssh_cmd(server, f"docker stop {n}")
            success(f"Stopped {len(running)} containers.")
    elif idx == 6 and running:
        if confirm(f"Restart {len(running)} running container(s)?"):
            log_action("Docker Bulk Restart", f"all {len(running)} running on {server_name}")
            for n in running:
                info(f"Restarting {n}...")
                _ssh_cmd(server, f"docker restart {n}")
            success(f"Restarted {len(running)} containers.")
    elif idx == 7 and stopped:
        if confirm(f"Start {len(stopped)} stopped container(s)?"):
            log_action("Docker Bulk Start", f"all {len(stopped)} stopped on {server_name}")
            for n in stopped:
                info(f"Starting {n}...")
                _ssh_cmd(server, f"docker start {n}")
            success(f"Started {len(stopped)} containers.")
    elif idx == 8 and running:
        _server_bulk_update(server, running, select=False)
    else:
        info("No containers to operate on.")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_bulk_update(server, running, select=True):
    """Pull latest images and restart containers in bulk."""
    server_name = server.get("name", "?")
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
    for cname in names:
        r = _ssh_cmd(server, f"docker inspect {cname} --format '{{{{.Config.Image}}}}' 2>/dev/null")
        if r.returncode == 0 and r.stdout.strip():
            images[cname] = r.stdout.strip()

    if not images:
        error("Could not determine images for selected containers.")
        return

    # Pull unique images first
    unique_images = list(set(images.values()))
    log_action("Docker Bulk Update", f"{len(names)} containers ({len(unique_images)} images) on {server_name}")
    info(f"Pulling {len(unique_images)} unique image(s)...")
    for i, img in enumerate(unique_images):
        info(f"  Pulling {img}... ({i + 1}/{len(unique_images)})")
        _ssh_tty(server, f"docker pull {img}")

    # Restart containers
    for i, cname in enumerate(names):
        info(f"Restarting {cname}... ({i + 1}/{len(names)})")
        _ssh_cmd(server, f"docker stop {cname}")
        _ssh_cmd(server, f"docker start {cname}")

    success(f"Updated {len(names)} containers.")


# ─── Live Logs ────────────────────────────────────────────────────────────

def _server_live_logs(server):
    result = _ssh_cmd(
        server,
        "docker ps --format '{{.Names}}' 2>/dev/null"
    )
    if result.returncode != 0 or not result.stdout.strip():
        warn("No running containers found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    containers = sorted([n.strip() for n in result.stdout.strip().split("\n") if n.strip()])
    choices = list(containers)
    choices.extend(["───────────────", "Multiple containers — combined live logs", "← Back"])
    idx = pick_option("Follow logs for:", choices)
    if idx == len(containers) + 2:
        return
    elif idx == len(containers):
        return  # separator
    elif idx == len(containers) + 1:
        _server_multi_container_log(server, containers)
        return

    cname = containers[idx]
    aidx = pick_option(f"Logs: {cname}", [
        "Follow (live)     — tail -f style",
        "Last 200 lines    — static view",
        "Last 50 lines     — static view",
        "Search logs       — grep for a term",
        "← Back",
    ])
    if aidx == 4:
        return
    elif aidx == 0:
        _ssh_tty(server, f"docker logs -f --tail 100 {cname}")
    elif aidx == 1:
        r = _ssh_cmd(server, f"docker logs --tail 200 {cname} 2>&1")
        if r.stdout.strip():
            scrollable_list(f"Logs: {cname}", r.stdout.strip().split("\n"))
    elif aidx == 2:
        r = _ssh_cmd(server, f"docker logs --tail 50 {cname} 2>&1")
        if r.stdout.strip():
            scrollable_list(f"Logs: {cname}", r.stdout.strip().split("\n"))
    elif aidx == 3:
        term = prompt_text("Search term:")
        if term:
            safe_term = term.replace("'", "'\\''")
            r = _ssh_cmd(server, f"docker logs {cname} 2>&1 | grep -i '{safe_term}' | tail -50")
            if r.stdout.strip():
                scrollable_list(f"Logs: {cname} (search: {term})", r.stdout.strip().split("\n"))
            else:
                warn(f"No matches for '{term}'.")
                input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _server_multi_container_log(server, containers):
    """Follow logs from multiple containers simultaneously."""
    choices = [f"{n:<25}" for n in containers]
    selected = pick_multi("Select containers to follow:", choices)
    if not selected:
        return
    names = [containers[i] for i in selected]
    if not confirm(f"Follow logs for {len(names)} container(s)?"):
        return
    parts = []
    for n in names:
        parts.append(f"docker logs -f --tail 20 {n} 2>&1 | sed 's/^/[{n}] /' &")
    cmd = " ".join(parts) + " wait"
    info(f"Following {len(names)} containers (Ctrl+C to exit)...")
    _ssh_tty(server, cmd)


# ─── SSH Shell ────────────────────────────────────────────────────────────

def _run_tool(server, tool):
    """Run an individual system tool for a server."""
    host = _ssh_host_arg(server)
    port = server.get("port", "") or None
    name = server.get("name", "?")
    if tool == "portmap":
        from homelab.modules.portmap import show_port_map
        show_port_map(host=host, port=port)
    elif tool == "services":
        from homelab.modules.services import show_services
        show_services(host=host, port=port)
    elif tool == "processes":
        from homelab.modules.processes import show_processes
        show_processes(host=host, port=port)
    elif tool == "firewall":
        from homelab.modules.firewall import show_firewall_rules
        show_firewall_rules(host=host, port=port)
    elif tool == "mounts":
        from homelab.modules.mounts import show_mounts
        show_mounts(host=host, port=port)
    elif tool == "volumes":
        from homelab.modules.volumes import docker_volumes
        docker_volumes(host=host, port=port, label=name)


def _server_system_tools(server):
    """System tools submenu for a Docker server."""
    host = _ssh_host_arg(server)
    port = server.get("port", "") or None
    name = server.get("name", "?")
    while True:
        idx = pick_option(f"{name} — System Tools:", [
            "Port Map          — listening ports and processes",
            "Systemd Services  — manage systemd units",
            "Process Explorer  — view and kill processes",
            "Firewall Rules    — iptables/nftables viewer",
            "Mount Monitor     — filesystem usage",
            "Crontab           — scheduled cron jobs",
            "Docker Volumes    — volume usage and cleanup",
            "───────────────",
            "★ Add to Favorites — pin a tool to the main menu",
            "← Back",
        ])
        if idx == 9:
            return
        elif idx in (7,):
            continue
        elif idx == 0:
            from homelab.modules.portmap import show_port_map
            show_port_map(host=host, port=port)
        elif idx == 1:
            from homelab.modules.services import show_services
            show_services(host=host, port=port)
        elif idx == 2:
            from homelab.modules.processes import show_processes
            show_processes(host=host, port=port)
        elif idx == 3:
            from homelab.modules.firewall import show_firewall_rules
            show_firewall_rules(host=host, port=port)
        elif idx == 4:
            from homelab.modules.mounts import show_mounts
            show_mounts(host=host, port=port)
        elif idx == 5:
            _server_crontab(server)
        elif idx == 6:
            from homelab.modules.volumes import docker_volumes
            docker_volumes(host=host, port=port, label=name)
        elif idx == 8:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(DockerHostPlugin())


def _server_crontab(server):
    """View and edit crontab and cron directories."""
    while True:
        cmd = (
            "echo '=== User Crontab ===';"
            "crontab -l 2>/dev/null || echo '(no user crontab)';"
            "echo '';"
            "echo '=== /etc/cron.d/ ===';"
            "ls -la /etc/cron.d/ 2>/dev/null || echo '(not found)';"
            "echo '';"
            "echo '=== /etc/cron.daily/ ===';"
            "ls -la /etc/cron.daily/ 2>/dev/null || echo '(not found)';"
            "echo '';"
            "echo '=== /etc/cron.hourly/ ===';"
            "ls -la /etc/cron.hourly/ 2>/dev/null || echo '(not found)';"
            "echo '';"
            "echo '=== /etc/cron.weekly/ ===';"
            "ls -la /etc/cron.weekly/ 2>/dev/null || echo '(not found)';"
            "echo '';"
            "echo '=== /etc/cron.monthly/ ===';"
            "ls -la /etc/cron.monthly/ 2>/dev/null || echo '(not found)'"
        )
        result = _ssh_cmd(server, cmd)
        if result.returncode != 0:
            error("Failed to retrieve crontab information.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        lines = [f"  {line}" for line in result.stdout.strip().split("\n")]
        print()
        for line in lines:
            print(line)
        print()

        idx = pick_option(f"Crontab: {server.get('name', '?')}", [
            "Edit crontab       — open crontab -e in remote editor",
            "Edit cron file     — edit a file in /etc/cron.d/",
            "← Back",
        ])
        if idx == 2:
            return
        elif idx == 0:
            log_action("Edit Crontab", server.get("name", "?"))
            _ssh_tty(server, "crontab -e")
        elif idx == 1:
            _edit_cron_file(server)


def _edit_cron_file(server):
    """Pick a file from /etc/cron.d/ to edit."""
    result = _ssh_cmd(server, "ls /etc/cron.d/ 2>/dev/null")
    if result.returncode != 0 or not result.stdout.strip():
        warn("No files found in /etc/cron.d/")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    if not files:
        warn("No files found in /etc/cron.d/")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    choices = list(files)
    choices.append("← Back")
    idx = pick_option("Edit cron file:", choices)
    if idx >= len(files):
        return

    log_action("Edit Cron File", f"/etc/cron.d/{files[idx]} on {server.get('name', '?')}")
    _ssh_tty(server, f"nano /etc/cron.d/{files[idx]}")


def _server_ssh_shell(server):
    host = _ssh_host_arg(server)
    port = server.get("port", "")
    name = server.get("name", host)
    log_action("Docker SSH Shell", f"{name} ({host})")
    info(f"Connecting to {name}...")
    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", port])
    cmd.append(host)
    subprocess.run(cmd)
