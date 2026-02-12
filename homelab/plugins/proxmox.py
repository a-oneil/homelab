"""Proxmox VE plugin — VM and LXC container management, resource usage, console."""

import json
import ssl
import subprocess
import time
import urllib.request

from homelab.modules.auditlog import log_action
from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import (C, pick_option, scrollable_list, confirm, prompt_text, info, success,
                        error, warn, bar_chart, sparkline)

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None):
    """Make an authenticated API call to Proxmox."""
    base = CFG.get("proxmox_url", "").rstrip("/")
    token = CFG.get("proxmox_api_token", "")
    if not base or not token:
        return None
    url = f"{base}{endpoint}"
    headers = {
        "Authorization": f"PVEAPIToken={token}",
    }
    if data:
        payload = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    else:
        payload = None

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    # Allow self-signed certs
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"Proxmox API error: {e}")
        return None


class ProxmoxPlugin(Plugin):
    name = "Proxmox"
    key = "proxmox"

    def is_configured(self):
        return bool(CFG.get("proxmox_url") and CFG.get("proxmox_api_token") and CFG.get("proxmox_node"))

    def get_config_fields(self):
        return [
            ("proxmox_url", "Proxmox URL", "e.g. https://192.168.1.100:8006", False),
            ("proxmox_api_token", "Proxmox API Token", "e.g. root@pam!monitoring=uuid", True),
            ("proxmox_node", "Proxmox Node", "e.g. pve", False),
            ("proxmox_ssh_host", "Proxmox SSH Host", "e.g. root@192.168.1.100", False),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_menu_items(self):
        return [
            ("Proxmox              — manage VMs and containers", proxmox_menu),
        ]

    def get_actions(self):
        return {
            "Proxmox VMs": ("proxmox_vms", lambda: _list_guests("qemu", "VM")),
            "Proxmox Containers": ("proxmox_lxc", lambda: _list_guests("lxc", "Container")),
            "Proxmox Resource Usage": ("proxmox_resources", _resource_usage),
            "Proxmox Resource Trends": ("proxmox_trends", _resource_trends),
            "Proxmox Backups": ("proxmox_backups", _backup_browser),
            "Proxmox HA Status": ("proxmox_ha", _cluster_ha_status),
            "Proxmox Tasks": ("proxmox_tasks", _recent_tasks),
        }

    def resolve_favorite(self, fav):
        ftype = fav.get("type")
        if ftype in ("proxmox_vm", "proxmox_lxc"):
            vmid = fav["id"]
            guest_type = "qemu" if ftype == "proxmox_vm" else "lxc"
            label = "VM" if ftype == "proxmox_vm" else "Container"
            return lambda v=vmid, g=guest_type, lbl=label: _guest_actions(v, g, lbl)


def _fetch_stats():
    node = CFG.get("proxmox_node", "")
    # Count VMs
    vm_data = _api(f"/api2/json/nodes/{node}/qemu")
    lxc_data = _api(f"/api2/json/nodes/{node}/lxc")
    vms = vm_data.get("data", []) if vm_data else []
    lxcs = lxc_data.get("data", []) if lxc_data else []
    running_vms = sum(1 for v in vms if v.get("status") == "running")
    running_lxc = sum(1 for c in lxcs if c.get("status") == "running")
    total = len(vms) + len(lxcs)
    running = running_vms + running_lxc
    _HEADER_CACHE["stats"] = f"Proxmox: {total} guests ({running} running)"
    _HEADER_CACHE["timestamp"] = time.time()


def _get_proxmox_ssh_host():
    """Return the SSH host for the Proxmox node."""
    host = CFG.get("proxmox_ssh_host", "")
    if host:
        return host
    # Fall back to extracting IP from the API URL
    url = CFG.get("proxmox_url", "")
    return "root@" + url.replace("https://", "").replace("http://", "").split(":")[0]


def proxmox_menu():
    while True:
        idx = pick_option("Proxmox:", [
            "Console Access        — SSH or noVNC to a guest",
            "List Containers (LXC) — all LXC containers with status",
            "List VMs              — all QEMU VMs with status",
            "Resource Usage        — CPU, memory, storage per guest",
            "Resource Trends       — CPU/memory sparkline trends",
            "Backups               — browse and manage guest backups",
            "Cluster HA Status     — high availability resources",
            "Recent Tasks          — node task history and logs",
            "SSH Shell             — open a terminal on the Proxmox host",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 11:
            return
        elif idx == 9:
            continue
        elif idx == 10:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(ProxmoxPlugin())
        elif idx == 0:
            _console_access()
        elif idx == 1:
            _list_guests("lxc", "Container")
        elif idx == 2:
            _list_guests("qemu", "VM")
        elif idx == 3:
            _resource_usage()
        elif idx == 4:
            _resource_trends()
        elif idx == 5:
            _backup_browser()
        elif idx == 6:
            _cluster_ha_status()
        elif idx == 7:
            _recent_tasks()
        elif idx == 8:
            _proxmox_ssh_shell()


def _proxmox_ssh_shell():
    """Open an interactive SSH session to the Proxmox host."""
    host = _get_proxmox_ssh_host()
    log_action("SSH Shell", f"Proxmox ({host})")
    info(f"Connecting to {host}...")
    subprocess.run(["ssh", "-t", host])


def _guest_actions(vmid, guest_type, label):
    """Show actions for a single Proxmox guest (used by list and favorites)."""
    node = CFG.get("proxmox_node", "")
    data = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/current")
    if not data or not data.get("data"):
        error(f"{label} {vmid} not found.")
        return
    g = data["data"]
    name = g.get("name", "unnamed")
    status = g.get("status", "?")
    is_running = status == "running"
    is_template = g.get("template", 0) == 1

    if is_template:
        action_choices = ["Deploy from Template", "★ Favorite", "← Back"]
    elif is_running:
        action_choices = [
            "Shutdown", "Stop (force)", "Reboot", "Console (SSH)",
            "Snapshots", "Backups", "Disk Resize", "Clone",
            "★ Favorite", "← Back",
        ]
    else:
        action_choices = [
            "Start", "Snapshots", "Backups", "Clone",
            "Convert to Template", "★ Favorite", "← Back",
        ]

    action = pick_option(f"{label}: {name} (VMID {vmid}) — {status}", action_choices)
    action_label = action_choices[action]

    if action_label == "← Back":
        return
    elif action_label == "★ Favorite":
        from homelab.plugins import add_item_favorite
        ftype = "proxmox_vm" if guest_type == "qemu" else "proxmox_lxc"
        add_item_favorite(ftype, str(vmid), f"Proxmox {label}: {name}")
    elif action_label == "Start":
        log_action(f"{label} Start", f"{vmid} ({name})")
        result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/start", method="POST")
        if result:
            success(f"Starting {name}...")
        else:
            error(f"Failed to start {name}")
    elif action_label == "Shutdown":
        log_action(f"{label} Shutdown", f"{vmid} ({name})")
        result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/shutdown", method="POST")
        if result:
            success(f"Shutting down {name}...")
        else:
            error(f"Failed to shutdown {name}")
    elif action_label == "Stop (force)":
        if confirm(f"Force stop {name}?", default_yes=False):
            log_action(f"{label} Force Stop", f"{vmid} ({name})")
            result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/stop", method="POST")
            if result:
                success(f"Stopping {name}...")
    elif action_label == "Reboot":
        log_action(f"{label} Reboot", f"{vmid} ({name})")
        result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/reboot", method="POST")
        if result:
            success(f"Rebooting {name}...")
    elif action_label == "Console (SSH)":
        _open_console(vmid, guest_type, name)
    elif action_label == "Snapshots":
        _snapshot_menu(vmid, guest_type, name)
    elif action_label == "Backups":
        _guest_backups(vmid, name)
    elif action_label == "Disk Resize":
        _disk_resize(vmid, guest_type, name)
    elif action_label == "Clone":
        _clone_guest(vmid, guest_type, name)
    elif action_label == "Convert to Template":
        _convert_to_template(vmid, guest_type, name)
    elif action_label == "Deploy from Template":
        _clone_guest(vmid, guest_type, name)


def _list_guests(guest_type, label):
    node = CFG.get("proxmox_node", "")
    while True:
        data = _api(f"/api2/json/nodes/{node}/{guest_type}")
        if not data or not data.get("data"):
            warn(f"No {label}s found.")
            return

        guests = sorted(data["data"], key=lambda g: g.get("vmid", 0))
        choices = []
        for g in guests:
            vmid = g.get("vmid", "?")
            name = g.get("name", "unnamed")
            status = g.get("status", "?")
            mem = g.get("maxmem", 0) / (1024**3)
            choices.append(f"{vmid}  {name:<20} [{status}]  {mem:.0f}GB RAM")
        choices.append("← Back")

        idx = pick_option(f"{label}s:", choices)
        if idx >= len(guests):
            return

        g = guests[idx]
        _guest_actions(g.get("vmid"), guest_type, label)


# ─── Resource Usage ────────────────────────────────────────────────────────

def _resource_usage():
    """Show CPU, memory, and storage usage per guest with bar charts."""
    node = CFG.get("proxmox_node", "")

    # Fetch node status
    node_status = _api(f"/api2/json/nodes/{node}/status")
    if node_status and node_status.get("data"):
        ns = node_status["data"]
        cpu_pct = ns.get("cpu", 0) * 100
        mem_used = ns.get("memory", {}).get("used", 0)
        mem_total = ns.get("memory", {}).get("total", 1)
        root_used = ns.get("rootfs", {}).get("used", 0)
        root_total = ns.get("rootfs", {}).get("total", 1)

        print(f"\n  {C.BOLD}Node: {node}{C.RESET}\n")
        print(f"  CPU:     {bar_chart(cpu_pct, 100, width=25)}  {cpu_pct:.1f}%")
        print(f"  Memory:  {bar_chart(mem_used, mem_total, width=25)}  "
              f"{mem_used / (1024**3):.1f} / {mem_total / (1024**3):.1f} GB")
        print(f"  Root FS: {bar_chart(root_used, root_total, width=25)}  "
              f"{root_used / (1024**3):.1f} / {root_total / (1024**3):.1f} GB")
        print()

    # Fetch all guests
    vm_data = _api(f"/api2/json/nodes/{node}/qemu")
    lxc_data = _api(f"/api2/json/nodes/{node}/lxc")
    guests = []
    if vm_data and vm_data.get("data"):
        for g in vm_data["data"]:
            g["_type"] = "VM"
            guests.append(g)
    if lxc_data and lxc_data.get("data"):
        for g in lxc_data["data"]:
            g["_type"] = "CT"
            guests.append(g)

    if not guests:
        warn("No guests found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"  {C.BOLD}{'Guest':<25} {'Type':>4}  {'CPU':>12}  {'Memory':>30}{C.RESET}")
    print(f"  {'─' * 75}")

    for g in sorted(guests, key=lambda x: x.get("name", "")):
        name = g.get("name", "unnamed")
        gtype = g.get("_type", "?")
        status = g.get("status", "?")

        if status != "running":
            print(f"  {name:<25} {gtype:>4}  {C.DIM}[{status}]{C.RESET}")
            continue

        cpu = g.get("cpu", 0) * 100
        maxcpu = g.get("maxcpu", 1)
        mem = g.get("mem", 0)
        maxmem = g.get("maxmem", 1)
        mem_gb = mem / (1024 ** 3)
        maxmem_gb = maxmem / (1024 ** 3)

        cpu_bar = bar_chart(cpu, 100 * maxcpu, width=8)
        mem_bar = bar_chart(mem, maxmem, width=10)

        print(f"  {name:<25} {gtype:>4}  {cpu_bar}  "
              f"{mem_bar} {mem_gb:.1f}/{maxmem_gb:.0f}GB")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Console Access ────────────────────────────────────────────────────────

def _console_access():
    """Open SSH or noVNC console to a guest."""
    node = CFG.get("proxmox_node", "")

    # Collect running guests
    vm_data = _api(f"/api2/json/nodes/{node}/qemu")
    lxc_data = _api(f"/api2/json/nodes/{node}/lxc")
    guests = []
    if vm_data and vm_data.get("data"):
        for g in vm_data["data"]:
            if g.get("status") == "running":
                guests.append({"vmid": g["vmid"], "name": g.get("name", "unnamed"), "type": "qemu"})
    if lxc_data and lxc_data.get("data"):
        for g in lxc_data["data"]:
            if g.get("status") == "running":
                guests.append({"vmid": g["vmid"], "name": g.get("name", "unnamed"), "type": "lxc"})

    if not guests:
        warn("No running guests found.")
        return

    choices = [f"{g['name']} (VMID {g['vmid']}, {g['type']})" for g in guests]
    choices.append("← Back")
    idx = pick_option("Connect to:", choices)
    if idx >= len(guests):
        return

    g = guests[idx]
    _open_console(g["vmid"], g["type"], g["name"])


def _open_console(vmid, guest_type, name):
    """Open console connection to a guest."""
    if guest_type == "lxc":
        choices = ["SSH (via Proxmox)", "Exec shell (pct enter)", "← Back"]
    else:
        choices = ["SSH (direct)", "← Back"]

    idx = pick_option(f"Console: {name} (VMID {vmid})", choices)
    choice = choices[idx]

    if choice == "← Back":
        return
    elif choice == "SSH (direct)":
        # Try to get the guest's IP from the QEMU agent
        node = CFG.get("proxmox_node", "")
        net = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/agent/network-get-interfaces")
        ip = None
        if net and net.get("data", {}).get("result"):
            for iface in net["data"]["result"]:
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") == "ipv4" and not addr["ip-address"].startswith("127."):
                        ip = addr["ip-address"]
                        break
                if ip:
                    break

        if ip:
            log_action("VM Console SSH", f"{vmid} ({name}) at {ip}")
            info(f"Connecting to {name} at {ip}...")
            subprocess.run(["ssh", "-t", ip])
        else:
            warn("Could not determine guest IP. Is the QEMU agent running?")
            manual_ip = prompt_text("Enter IP manually (or leave blank to cancel):")
            if manual_ip:
                log_action("VM Console SSH", f"{vmid} ({name}) at {manual_ip}")
                subprocess.run(["ssh", "-t", manual_ip])
    elif choice == "SSH (via Proxmox)":
        proxmox_host = _get_proxmox_ssh_host()
        log_action("LXC Console SSH", f"{vmid} ({name}) via {proxmox_host}")
        info(f"Opening shell in LXC {name}...")
        subprocess.run(["ssh", "-t", proxmox_host, f"pct enter {vmid}"])
    elif choice == "Exec shell (pct enter)":
        proxmox_host = _get_proxmox_ssh_host()
        log_action("LXC Console Exec", f"{vmid} ({name}) via {proxmox_host}")
        info(f"Opening shell in LXC {name}...")
        subprocess.run(["ssh", "-t", proxmox_host, f"pct enter {vmid}"])


# ─── Snapshot Management ──────────────────────────────────────────────────

def _snapshot_menu(vmid, guest_type, name):
    """List, create, rollback, and delete snapshots for a guest."""
    node = CFG.get("proxmox_node", "")
    while True:
        data = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/snapshot")
        snapshots = data.get("data", []) if data else []
        # Filter out the "current" pseudo-snapshot
        snapshots = [s for s in snapshots if s.get("name") != "current"]

        choices = []
        for s in snapshots:
            snap_name = s.get("name", "?")
            desc = s.get("description", "")[:30]
            snap_time = s.get("snaptime", 0)
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(snap_time)) if snap_time else "?"
            choices.append(f"{snap_name:<20} {ts}  {desc}")

        choices.extend([
            "───────────────",
            "+ Create Snapshot",
            "← Back",
        ])

        hdr = f"\n  {C.BOLD}Snapshots: {name} (VMID {vmid}){C.RESET}  ({len(snapshots)} snapshots)\n"
        idx = pick_option("", choices, header=hdr)

        if choices[idx] == "← Back":
            return
        elif "────" in choices[idx]:
            continue
        elif choices[idx] == "+ Create Snapshot":
            snap_name = prompt_text("Snapshot name:") or f"snap_{int(time.time())}"
            desc = prompt_text("Description (optional):")
            payload = {"snapname": snap_name}
            if desc:
                payload["description"] = desc
            log_action("Snapshot Create", f"{vmid} ({name}) — {snap_name}")
            result = _api(
                f"/api2/json/nodes/{node}/{guest_type}/{vmid}/snapshot",
                method="POST", data=payload,
            )
            if result:
                success(f"Snapshot created: {snap_name}")
            else:
                error("Snapshot failed")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        elif idx < len(snapshots):
            _snapshot_actions(vmid, guest_type, name, snapshots[idx])


def _snapshot_actions(vmid, guest_type, name, snapshot):
    """Show actions for a single snapshot (rollback, delete)."""
    node = CFG.get("proxmox_node", "")
    snap_name = snapshot.get("name", "?")
    desc = snapshot.get("description", "")
    snap_time = snapshot.get("snaptime", 0)
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(snap_time)) if snap_time else "?"

    print(f"\n  {C.BOLD}Snapshot: {snap_name}{C.RESET}")
    print(f"  VM/CT: {name} (VMID {vmid})")
    print(f"  Created: {ts}")
    if desc:
        print(f"  Description: {desc}")

    aidx = pick_option(f"Snapshot: {snap_name}", [
        "Rollback       — restore VM to this snapshot",
        "Delete         — remove this snapshot",
        "← Back",
    ])
    if aidx == 0:
        if confirm(f"Rollback {name} to snapshot '{snap_name}'? Current state will be lost.",
                   default_yes=False):
            log_action("Snapshot Rollback", f"{vmid} ({name}) — {snap_name}")
            result = _api(
                f"/api2/json/nodes/{node}/{guest_type}/{vmid}/snapshot/{snap_name}/rollback",
                method="POST",
            )
            if result:
                success(f"Rolling back to {snap_name}...")
            else:
                error("Rollback failed")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif aidx == 1:
        if confirm(f"Delete snapshot '{snap_name}'? This cannot be undone.",
                   default_yes=False):
            log_action("Snapshot Delete", f"{vmid} ({name}) — {snap_name}")
            result = _api(
                f"/api2/json/nodes/{node}/{guest_type}/{vmid}/snapshot/{snap_name}",
                method="DELETE",
            )
            if result:
                success(f"Deleted snapshot: {snap_name}")
            else:
                error("Delete failed")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Backup Management ───────────────────────────────────────────────────

def _backup_browser():
    """Browse and manage guest backups across all storage."""
    node = CFG.get("proxmox_node", "")
    while True:
        storages = _api(f"/api2/json/nodes/{node}/storage")
        if not storages or not storages.get("data"):
            warn("No storage found.")
            return

        backup_storages = [
            s for s in storages["data"]
            if "backup" in s.get("content", "").split(",")
        ]
        if not backup_storages:
            warn("No backup-capable storage found.")
            return

        choices = []
        for s in backup_storages:
            sname = s.get("storage", "?")
            used = s.get("used", 0) / (1024 ** 3)
            total = s.get("total", 0) / (1024 ** 3)
            pct = s.get("used_fraction", 0) * 100
            choices.append(f"{sname:<20} {used:.1f}/{total:.1f} GB ({pct:.0f}%)")
        choices.append("← Back")

        idx = pick_option("Backup Storage:", choices)
        if idx >= len(backup_storages):
            return

        _storage_backups(backup_storages[idx].get("storage", ""))


def _storage_backups(storage_name):
    """List backup files in a storage."""
    node = CFG.get("proxmox_node", "")
    while True:
        data = _api(f"/api2/json/nodes/{node}/storage/{storage_name}/content?content=backup")
        if not data or not data.get("data"):
            warn("No backups found in this storage.")
            return

        backups = sorted(data["data"], key=lambda b: b.get("ctime", 0), reverse=True)
        choices = []
        for b in backups:
            vmid_val = b.get("vmid", "?")
            size = b.get("size", 0) / (1024 ** 3)
            ctime = b.get("ctime", 0)
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ctime)) if ctime else "?"
            fmt = b.get("format", "?")
            choices.append(f"VMID {vmid_val:<6} {ts}  {size:.2f} GB  [{fmt}]")

        choices.extend([
            "───────────────",
            "+ Trigger Backup     — start vzdump for a guest",
            "← Back",
        ])

        hdr = f"\n  {C.BOLD}Backups: {storage_name}{C.RESET}  ({len(backups)} backups)\n"
        idx = pick_option("", choices, header=hdr)

        if choices[idx] == "← Back":
            return
        elif "────" in choices[idx]:
            continue
        elif "+ Trigger Backup" in choices[idx]:
            _trigger_backup(storage_name)
        elif idx < len(backups):
            _backup_detail(backups[idx], storage_name)


def _backup_detail(backup, storage_name):
    """Show backup info with option to delete."""
    node = CFG.get("proxmox_node", "")
    volid = backup.get("volid", "?")
    vmid_val = backup.get("vmid", "?")
    size_gb = backup.get("size", 0) / (1024 ** 3)
    ctime = backup.get("ctime", 0)
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ctime)) if ctime else "?"

    print(f"\n  {C.BOLD}Backup Details{C.RESET}")
    print(f"  VMID: {vmid_val}")
    print(f"  Volume: {volid}")
    print(f"  Size: {size_gb:.2f} GB")
    print(f"  Created: {ts}")
    print(f"  Format: {backup.get('format', '?')}")

    aidx = pick_option(f"Backup VMID {vmid_val}:", [
        "Delete backup",
        "← Back",
    ])
    if aidx == 0:
        if confirm(f"Delete backup {volid}? This cannot be undone.", default_yes=False):
            log_action("Backup Delete", f"VMID {vmid_val} — {volid}")
            result = _api(
                f"/api2/json/nodes/{node}/storage/{storage_name}/content/{volid}",
                method="DELETE",
            )
            if result is not None:
                success(f"Deleted backup: {volid}")
            else:
                error("Failed to delete backup.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _trigger_backup(storage_name):
    """Start a vzdump backup for a selected guest."""
    node = CFG.get("proxmox_node", "")
    vm_data = _api(f"/api2/json/nodes/{node}/qemu")
    lxc_data = _api(f"/api2/json/nodes/{node}/lxc")
    guests = []
    if vm_data and vm_data.get("data"):
        for g in vm_data["data"]:
            guests.append({"vmid": g["vmid"], "name": g.get("name", "unnamed"), "type": "VM"})
    if lxc_data and lxc_data.get("data"):
        for g in lxc_data["data"]:
            guests.append({"vmid": g["vmid"], "name": g.get("name", "unnamed"), "type": "CT"})

    if not guests:
        warn("No guests found.")
        return

    choices = [f"{g['vmid']}  {g['name']:<20} [{g['type']}]" for g in guests]
    choices.append("← Back")
    idx = pick_option("Backup which guest?", choices)
    if idx >= len(guests):
        return

    guest = guests[idx]
    mode_idx = pick_option(f"Backup mode for {guest['name']}:", [
        "snapshot  — online backup (recommended)",
        "suspend   — suspend VM during backup",
        "stop      — stop VM during backup",
        "← Back",
    ])
    if mode_idx == 3:
        return
    modes = ["snapshot", "suspend", "stop"]
    mode = modes[mode_idx]

    if confirm(f"Start {mode} backup of {guest['name']} (VMID {guest['vmid']}) to {storage_name}?"):
        log_action("Backup Trigger", f"VMID {guest['vmid']} ({guest['name']}) mode={mode}")
        payload = {"vmid": guest["vmid"], "storage": storage_name, "mode": mode}
        result = _api(f"/api2/json/nodes/{node}/vzdump", method="POST", data=payload)
        if result:
            success(f"Backup started for {guest['name']} (VMID {guest['vmid']})")
        else:
            error("Failed to start backup.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _guest_backups(vmid, name):
    """Show backups for a specific guest VMID."""
    node = CFG.get("proxmox_node", "")
    storages = _api(f"/api2/json/nodes/{node}/storage")
    if not storages or not storages.get("data"):
        warn("No storage found.")
        return

    all_backups = []
    for s in storages["data"]:
        if "backup" not in s.get("content", "").split(","):
            continue
        sname = s.get("storage", "")
        content = _api(
            f"/api2/json/nodes/{node}/storage/{sname}/content?content=backup&vmid={vmid}")
        if content and content.get("data"):
            for b in content["data"]:
                b["_storage"] = sname
                all_backups.append(b)

    if not all_backups:
        info(f"No backups found for {name} (VMID {vmid}).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    all_backups.sort(key=lambda b: b.get("ctime", 0), reverse=True)
    rows = []
    for b in all_backups:
        ctime = b.get("ctime", 0)
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ctime)) if ctime else "?"
        size = b.get("size", 0) / (1024 ** 3)
        storage = b.get("_storage", "?")
        rows.append(f"{ts}  {size:.2f} GB  [{storage}]")

    scrollable_list(f"Backups: {name} (VMID {vmid}) — {len(rows)} total", rows)


# ─── Guest Operations ────────────────────────────────────────────────────

def _disk_resize(vmid, guest_type, name):
    """Resize a disk of a running guest."""
    node = CFG.get("proxmox_node", "")
    config_data = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/config")
    if not config_data or not config_data.get("data"):
        error("Could not fetch guest config.")
        return

    disk_keys = []
    cfg = config_data["data"]
    for key, val in cfg.items():
        prefixes = ("virtio", "scsi", "ide", "sata", "rootfs", "mp")
        if any(key.startswith(p) for p in prefixes):
            if isinstance(val, str) and ":" in val:
                disk_keys.append((key, val))

    if not disk_keys:
        warn("No resizable disks found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    choices = [f"{key}  {val[:60]}" for key, val in disk_keys]
    choices.append("← Back")
    idx = pick_option(f"Resize disk on {name}:", choices)
    if idx >= len(disk_keys):
        return

    disk_name = disk_keys[idx][0]
    size_input = prompt_text("Increase size by (e.g. +10G, +500M):")
    if not size_input:
        return

    if not size_input.startswith("+"):
        size_input = "+" + size_input

    if confirm(f"Resize {disk_name} on {name} by {size_input}?"):
        log_action("Disk Resize", f"VMID {vmid} ({name}) {disk_name} {size_input}")
        result = _api(
            f"/api2/json/nodes/{node}/{guest_type}/{vmid}/resize",
            method="PUT", data={"disk": disk_name, "size": size_input})
        if result is not None:
            success(f"Resized {disk_name} by {size_input}")
        else:
            error("Resize failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _clone_guest(vmid, guest_type, name):
    """Clone a VM or LXC to a new VMID."""
    node = CFG.get("proxmox_node", "")
    new_vmid = prompt_text("New VMID (e.g. 200):")
    if not new_vmid:
        return
    try:
        new_vmid = int(new_vmid)
    except ValueError:
        error("VMID must be a number.")
        return

    new_name = prompt_text("Name for clone:", default=f"{name}-clone")
    if not new_name:
        return

    full_clone = confirm("Full clone? (No = linked clone)", default_yes=True)
    payload = {"newid": new_vmid, "name": new_name}
    if full_clone:
        payload["full"] = 1

    if confirm(f"Clone {name} (VMID {vmid}) to {new_name} (VMID {new_vmid})?"):
        log_action("Guest Clone", f"VMID {vmid} ({name}) → VMID {new_vmid} ({new_name})")
        result = _api(
            f"/api2/json/nodes/{node}/{guest_type}/{vmid}/clone",
            method="POST", data=payload)
        if result:
            success(f"Clone started: {new_name} (VMID {new_vmid})")
        else:
            error("Clone failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _convert_to_template(vmid, guest_type, name):
    """Convert a stopped guest to a template."""
    node = CFG.get("proxmox_node", "")
    if confirm(f"Convert {name} (VMID {vmid}) to a template? This cannot be undone.",
               default_yes=False):
        log_action("Convert to Template", f"VMID {vmid} ({name})")
        result = _api(
            f"/api2/json/nodes/{node}/{guest_type}/{vmid}/template",
            method="POST")
        if result is not None:
            success(f"Converted {name} to template.")
        else:
            error("Conversion failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Resource Trends ─────────────────────────────────────────────────────

def _resource_trends():
    """Show CPU/memory sparkline trends per guest (multi-sample)."""
    node = CFG.get("proxmox_node", "")
    samples = 5
    interval = 3
    print()

    history = {}
    for i in range(samples):
        if i > 0:
            time.sleep(interval)
        print(f"\r  Collecting sample {i + 1}/{samples}...", end="", flush=True)

        vm_data = _api(f"/api2/json/nodes/{node}/qemu")
        lxc_data = _api(f"/api2/json/nodes/{node}/lxc")
        guests = []
        if vm_data and vm_data.get("data"):
            guests.extend(vm_data["data"])
        if lxc_data and lxc_data.get("data"):
            guests.extend(lxc_data["data"])

        for g in guests:
            if g.get("status") != "running":
                continue
            gname = g.get("name", "unnamed")
            cpu_pct = g.get("cpu", 0) * 100
            mem = g.get("mem", 0)
            maxmem = g.get("maxmem", 1)
            mem_pct = (mem / maxmem * 100) if maxmem else 0

            if gname not in history:
                history[gname] = {"cpu": [], "mem": [], "maxmem_gb": maxmem / (1024 ** 3)}
            history[gname]["cpu"].append(cpu_pct)
            history[gname]["mem"].append(mem_pct)

    print("\r" + " " * 40 + "\r", end="")

    if not history:
        warn("No running guests found.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    max_name = min(28, max(len(n) for n in history.keys()))
    max_name = max(max_name, 5)

    print(
        f"  {C.BOLD}Proxmox — Guest Resource Trends{C.RESET}  "
        f"{C.DIM}({samples} samples, {interval}s interval){C.RESET}\n")
    print(f"  {C.DIM}{'Guest':<{max_name}}  {'CPU':^{samples + 7}}  {'Memory':^{samples + 10}}{C.RESET}")
    print(f"  {'─' * (max_name + samples * 2 + 25)}")

    for gname in sorted(history.keys()):
        h = history[gname]
        cpu_vals = h["cpu"]
        mem_vals = h["mem"]
        last_cpu = cpu_vals[-1] if cpu_vals else 0
        last_mem = mem_vals[-1] if mem_vals else 0
        maxmem_gb = h["maxmem_gb"]

        cpu_spark = sparkline(cpu_vals, width=samples)
        mem_spark = sparkline(mem_vals, width=samples)

        display = gname if len(gname) <= max_name else gname[:max_name - 1] + "…"

        if last_cpu > 50:
            cpu_color = C.RED
        elif last_cpu > 10:
            cpu_color = C.YELLOW
        else:
            cpu_color = C.GREEN

        print(
            f"  {display:<{max_name}}  "
            f"{cpu_color}{cpu_spark}{C.RESET} {last_cpu:>5.1f}%  "
            f"{C.ACCENT}{mem_spark}{C.RESET} {last_mem:>4.0f}% of {maxmem_gb:.0f}GB")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Cluster HA Status ───────────────────────────────────────────────────

def _cluster_ha_status():
    """Show HA cluster resources and their current status."""
    resources = _api("/api2/json/cluster/ha/resources")
    ha_status = _api("/api2/json/cluster/ha/status/current")

    print(f"\n  {C.BOLD}Cluster HA Status{C.RESET}\n")

    if ha_status and ha_status.get("data"):
        for item in ha_status["data"]:
            stype = item.get("type", "?")
            node_name = item.get("node", item.get("id", "?"))
            state = item.get("status", item.get("state", "?"))
            if state in ("online", "active"):
                dot = f"{C.GREEN}●{C.RESET}"
            elif state in ("standby", "maintenance"):
                dot = f"{C.YELLOW}●{C.RESET}"
            else:
                dot = f"{C.RED}●{C.RESET}"
            print(f"  {dot} {node_name:<20} [{stype}] {state}")
        print()

    if resources and resources.get("data"):
        print(f"  {C.BOLD}HA Resources:{C.RESET}\n")
        for r in resources["data"]:
            sid = r.get("sid", "?")
            state = r.get("state", "?")
            node_name = r.get("node", "?")
            group = r.get("group", "")
            if state == "started":
                dot = f"{C.GREEN}●{C.RESET}"
            elif state == "stopped":
                dot = f"{C.RED}●{C.RESET}"
            else:
                dot = f"{C.YELLOW}●{C.RESET}"
            print(f"  {dot} {sid:<20} [{state}] on {node_name}  group={group or 'none'}")
    else:
        info("No HA resources configured.")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Recent Tasks ────────────────────────────────────────────────────────

def _recent_tasks():
    """Show recent tasks on the Proxmox node."""
    node = CFG.get("proxmox_node", "")
    while True:
        data = _api(f"/api2/json/nodes/{node}/tasks?limit=30")
        if not data or not data.get("data"):
            warn("No tasks found.")
            return

        tasks = data["data"]
        choices = []
        for t in tasks:
            task_type = t.get("type", "?")
            status_str = t.get("status", "?")
            start_time = t.get("starttime", 0)
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(start_time)) if start_time else "?"
            user = t.get("user", "?")

            if status_str == "OK":
                icon = f"{C.GREEN}●{C.RESET}"
            elif status_str and "error" in status_str.lower():
                icon = f"{C.RED}●{C.RESET}"
            else:
                icon = f"{C.YELLOW}●{C.RESET}"

            choices.append(f"{icon} {ts}  {task_type:<15} {status_str:<10} {C.DIM}{user}{C.RESET}")

        choices.append("← Back")
        idx = pick_option(f"Recent Tasks ({len(tasks)}):", choices)
        if idx >= len(tasks):
            return

        _task_log(tasks[idx])


def _task_log(task):
    """Show the log output for a specific task."""
    node = CFG.get("proxmox_node", "")
    upid = task.get("upid", "")
    if not upid:
        return

    # URL-encode the UPID (contains colons and other special chars)
    import urllib.parse
    encoded_upid = urllib.parse.quote(upid, safe="")
    data = _api(f"/api2/json/nodes/{node}/tasks/{encoded_upid}/log?limit=100")
    if not data or not data.get("data"):
        warn("No log output.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    log_lines = [entry.get("t", "") for entry in data["data"]]
    task_type = task.get("type", "?")
    scrollable_list(f"Task Log: {task_type}", log_lines)
