"""Service Health Map — ASCII topology of all configured services and their status."""

import ssl
import subprocess
import urllib.request

from homelab.config import CFG
from homelab.ui import C, pick_option


def _check_http(url, timeout=5):
    """Return True if a URL responds with 2xx/3xx."""
    if not url:
        return None
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status < 400
    except Exception:
        return False


def _check_ssh(host, timeout=3):
    """Return True if SSH host is reachable."""
    if not host:
        return None
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=" + str(timeout),
             host, "echo ok"],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        return result.returncode == 0
    except Exception:
        return False


def _status_dot(status):
    """Return a colored status indicator."""
    if status is True:
        return f"{C.GREEN}●{C.RESET}"
    elif status is False:
        return f"{C.RED}●{C.RESET}"
    return f"{C.DIM}○{C.RESET}"  # Not configured / unknown


def _status_label(status):
    if status is True:
        return f"{C.GREEN}UP{C.RESET}"
    elif status is False:
        return f"{C.RED}DOWN{C.RESET}"
    return f"{C.DIM}N/A{C.RESET}"


def health_map(plugins):
    """Build and display the service health map."""
    from homelab.ui import info

    while True:
        info("Checking service health (this may take a few seconds)...")
        print()

        lines = []
        lines.append(f"\n  {C.ACCENT}{C.BOLD}╔══════════════════════════════════╗{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}║       SERVICE HEALTH MAP         ║{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}╚══════════════════════════════════╝{C.RESET}")
        lines.append("")
        lines.append(f"  {C.GREEN}●{C.RESET} UP   {C.RED}●{C.RESET} DOWN   {C.DIM}○{C.RESET} Not configured")
        lines.append("")

        # Infrastructure layer
        lines.append(f"  {C.BOLD}Infrastructure{C.RESET}")
        lines.append(f"  {'─' * 50}")

        # Unraid
        unraid_host = CFG.get("unraid_ssh_host", "")
        if unraid_host:
            status = _check_ssh(unraid_host)
            dot = _status_dot(status)
            lines.append(f"  {dot} Unraid Server          {C.DIM}{unraid_host}{C.RESET}  {_status_label(status)}")

            # Docker containers (if Unraid is up)
            if status:
                try:
                    result = subprocess.run(
                        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
                         unraid_host,
                         "docker ps --format '{{.Names}}\\t{{.Status}}' 2>/dev/null | head -15"],
                        capture_output=True, text=True, timeout=8,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        containers = result.stdout.strip().split("\n")
                        running = sum(1 for c in containers if "Up" in c)
                        total = len(containers)
                        lines.append(f"  │  {C.GREEN}●{C.RESET} Docker: {running}/{total} containers running")
                except Exception:
                    pass

        # Proxmox
        proxmox_url = CFG.get("proxmox_url", "")
        if proxmox_url:
            status = _check_http(proxmox_url)
            dot = _status_dot(status)
            lines.append(f"  {dot} Proxmox VE             {C.DIM}{proxmox_url}{C.RESET}  {_status_label(status)}")

        lines.append("")

        # Network layer
        has_network = any(CFG.get(k) for k in ["unifi_url", "opnsense_url", "tailscale_enabled"])
        if has_network:
            lines.append(f"  {C.BOLD}Network{C.RESET}")
            lines.append(f"  {'─' * 50}")

            if CFG.get("unifi_url"):
                status = _check_http(CFG["unifi_url"])
                dot = _status_dot(status)
                lines.append(f"  {dot} UniFi Controller       {C.DIM}{CFG['unifi_url']}{C.RESET}  {_status_label(status)}")

            if CFG.get("opnsense_url"):
                status = _check_http(CFG["opnsense_url"])
                dot = _status_dot(status)
                lines.append(f"  {dot} OPNsense               {C.DIM}{CFG['opnsense_url']}{C.RESET}  {_status_label(status)}")

            if CFG.get("tailscale_enabled"):
                try:
                    result = subprocess.run(
                        ["tailscale", "status", "--json"],
                        capture_output=True, text=True, timeout=5,
                    )
                    ts_up = result.returncode == 0
                except Exception:
                    ts_up = False
                dot = _status_dot(ts_up)
                lines.append(f"  {dot} Tailscale VPN          {_status_label(ts_up)}")

            lines.append("")

        # Services layer
        services = [
            ("homeassistant_url", "Home Assistant"),
            ("uptimekuma_url", "Uptime Kuma"),
            ("npm_url", "Nginx Proxy Manager"),
            ("forgejo_url", "Forgejo"),
            ("immich_url", "Immich"),
            ("syncthing_url", "Syncthing"),
        ]
        active_services = [(key, name) for key, name in services if CFG.get(key)]
        if active_services:
            lines.append(f"  {C.BOLD}Services{C.RESET}")
            lines.append(f"  {'─' * 50}")
            for key, name in active_services:
                url = CFG[key]
                status = _check_http(url)
                dot = _status_dot(status)
                lines.append(f"  {dot} {name:<22} {C.DIM}{url}{C.RESET}  {_status_label(status)}")
            lines.append("")

        # Media layer
        media = [
            ("plex_url", "Plex"),
            ("jellyfin_url", "Jellyfin"),
            ("sonarr_url", "Sonarr"),
            ("radarr_url", "Radarr"),
            ("lidarr_url", "Lidarr"),
            ("sabnzbd_url", "SABnzbd"),
            ("deluge_url", "Deluge"),
        ]
        active_media = [(key, name) for key, name in media if CFG.get(key)]
        if active_media:
            lines.append(f"  {C.BOLD}Media{C.RESET}")
            lines.append(f"  {'─' * 50}")
            for key, name in active_media:
                url = CFG[key]
                status = _check_http(url)
                dot = _status_dot(status)
                lines.append(f"  {dot} {name:<22} {C.DIM}{url}{C.RESET}  {_status_label(status)}")
            lines.append("")

        lines.append("")
        header = "\n".join(lines)
        idx = pick_option("", ["Refresh", "← Back"], header=header)
        if idx == 1:
            return
