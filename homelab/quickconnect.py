"""Quick Connect — unified SSH menu across all configured hosts."""

import subprocess

from homelab.config import CFG, save_config
from homelab.ui import C, pick_option, prompt_text, info, success, warn


def _gather_hosts():
    """Collect SSH hosts from all configured plugins + custom hosts."""
    hosts = []

    # Unraid
    unraid_host = CFG.get("unraid_ssh_host", "")
    if unraid_host:
        hosts.append({"name": "Unraid", "host": unraid_host})

    # Proxmox
    proxmox_host = CFG.get("proxmox_ssh_host", "")
    if not proxmox_host:
        url = CFG.get("proxmox_url", "")
        if url:
            proxmox_host = "root@" + url.replace("https://", "").replace("http://", "").split(":")[0]
    if proxmox_host:
        hosts.append({"name": "Proxmox", "host": proxmox_host})

    # Home Assistant
    ha_host = CFG.get("homeassistant_ssh_host", "")
    if ha_host:
        port = str(CFG.get("homeassistant_ssh_port", "")).strip()
        hosts.append({"name": "Home Assistant", "host": ha_host, "port": port})

    # Custom hosts from config
    for custom in CFG.get("ssh_hosts", []):
        hosts.append(custom)

    return hosts


def quick_connect_menu():
    """Show unified SSH menu with all available hosts."""
    while True:
        hosts = _gather_hosts()

        choices = []
        for h in hosts:
            name = h.get("name", "?")
            host = h.get("host", "?")
            port = h.get("port", "")
            port_str = f" :{port}" if port else ""
            choices.append(f"{name:<20} {C.DIM}{host}{port_str}{C.RESET}")

        choices.extend([
            "───────────────",
            "+ Add Custom Host",
            "- Remove Custom Host",
            "← Back",
        ])

        idx = pick_option("Quick Connect:", choices)

        if idx == len(hosts) + 3:  # Back
            return
        elif idx == len(hosts):  # Separator
            continue
        elif idx == len(hosts) + 1:  # Add custom
            _add_custom_host()
        elif idx == len(hosts) + 2:  # Remove custom
            _remove_custom_host()
        elif idx < len(hosts):
            _connect(hosts[idx])


def _connect(host_info):
    """SSH into a host."""
    host = host_info.get("host", "")
    port = host_info.get("port", "")
    name = host_info.get("name", host)

    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.append(host)

    info(f"Connecting to {name} ({host}" + (f" port {port}" if port else "") + ")...")
    subprocess.run(cmd)


def _add_custom_host():
    """Add a custom SSH host to the config."""
    name = prompt_text("Display name (e.g. Pi, NAS-2):")
    if not name:
        return
    host = prompt_text("SSH host (e.g. user@192.168.1.50):")
    if not host:
        return
    port = prompt_text("Port (leave blank for 22):")

    entry = {"name": name, "host": host}
    if port:
        entry["port"] = port

    custom = CFG.get("ssh_hosts", [])
    custom.append(entry)
    CFG["ssh_hosts"] = custom
    save_config(CFG)
    success(f"Added: {name} ({host})")


def _remove_custom_host():
    """Remove a custom SSH host from the config."""
    custom = CFG.get("ssh_hosts", [])
    if not custom:
        warn("No custom hosts to remove.")
        return

    choices = [f"{h['name']} ({h['host']})" for h in custom]
    choices.append("Cancel")
    idx = pick_option("Remove which host?", choices)
    if idx >= len(custom):
        return

    removed = custom.pop(idx)
    CFG["ssh_hosts"] = custom
    save_config(CFG)
    success(f"Removed: {removed['name']}")
