"""Proxmox VE plugin — VM and LXC container management, resource usage, console."""

import json
import ssl
import subprocess
import time
import urllib.request

from homelab.config import CFG
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, confirm, prompt_text, info, success, error, warn, bar_chart

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
            "List VMs              — all QEMU VMs with status",
            "List Containers (LXC) — all LXC containers with status",
            "Resource Usage        — CPU, memory, storage per guest",
            "Console Access        — SSH or noVNC to a guest",
            "SSH Shell             — open a terminal on the Proxmox host",
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        if idx == 7:
            return
        elif idx == 5:
            continue
        elif idx == 6:
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(ProxmoxPlugin())
        elif idx == 0:
            _list_guests("qemu", "VM")
        elif idx == 1:
            _list_guests("lxc", "Container")
        elif idx == 2:
            _resource_usage()
        elif idx == 3:
            _console_access()
        elif idx == 4:
            _proxmox_ssh_shell()


def _proxmox_ssh_shell():
    """Open an interactive SSH session to the Proxmox host."""
    host = _get_proxmox_ssh_host()
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

    if is_running:
        action_choices = [
            "Shutdown", "Stop (force)", "Reboot", "Console (SSH)",
            "Snapshot", "★ Favorite", "← Back",
        ]
    else:
        action_choices = ["Start", "Snapshot", "★ Favorite", "← Back"]

    action = pick_option(f"{label}: {name} (VMID {vmid}) — {status}", action_choices)
    action_label = action_choices[action]

    if action_label == "← Back":
        return
    elif action_label == "★ Favorite":
        from homelab.plugins import add_item_favorite
        ftype = "proxmox_vm" if guest_type == "qemu" else "proxmox_lxc"
        add_item_favorite(ftype, str(vmid), f"Proxmox {label}: {name}")
    elif action_label == "Start":
        result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/start", method="POST")
        if result:
            success(f"Starting {name}...")
        else:
            error(f"Failed to start {name}")
    elif action_label == "Shutdown":
        result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/shutdown", method="POST")
        if result:
            success(f"Shutting down {name}...")
        else:
            error(f"Failed to shutdown {name}")
    elif action_label == "Stop (force)":
        if confirm(f"Force stop {name}?", default_yes=False):
            result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/stop", method="POST")
            if result:
                success(f"Stopping {name}...")
    elif action_label == "Reboot":
        result = _api(f"/api2/json/nodes/{node}/{guest_type}/{vmid}/status/reboot", method="POST")
        if result:
            success(f"Rebooting {name}...")
    elif action_label == "Console (SSH)":
        _open_console(vmid, guest_type, name)
    elif action_label == "Snapshot":
        snap_name = prompt_text("Snapshot name:") or f"snap_{int(time.time())}"
        result = _api(
            f"/api2/json/nodes/{node}/{guest_type}/{vmid}/snapshot",
            method="POST",
            data={"snapname": snap_name},
        )
        if result:
            success(f"Snapshot created: {snap_name}")
        else:
            error("Snapshot failed")


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

        print(f"  {name:<25} {gtype:>4}  {cpu_bar} {cpu:.0f}%  "
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
            info(f"Connecting to {name} at {ip}...")
            subprocess.run(["ssh", "-t", ip])
        else:
            warn("Could not determine guest IP. Is the QEMU agent running?")
            manual_ip = prompt_text("Enter IP manually (or leave blank to cancel):")
            if manual_ip:
                subprocess.run(["ssh", "-t", manual_ip])
    elif choice == "SSH (via Proxmox)":
        proxmox_host = _get_proxmox_ssh_host()
        info(f"Opening shell in LXC {name}...")
        subprocess.run(["ssh", "-t", proxmox_host, f"pct enter {vmid}"])
    elif choice == "Exec shell (pct enter)":
        proxmox_host = _get_proxmox_ssh_host()
        info(f"Opening shell in LXC {name}...")
        subprocess.run(["ssh", "-t", proxmox_host, f"pct enter {vmid}"])
