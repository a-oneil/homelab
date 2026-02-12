"""Health Monitor — check for unhealthy containers, high CPU, low disk, API reachability, SSL expiry."""

import ssl
import socket
import time
import urllib.request
from datetime import datetime, timezone

from homelab.config import CFG
from homelab.ui import C

_ALERT_CACHE = {"timestamp": 0, "alerts": []}
_CACHE_TTL = 300  # 5 minutes


def get_health_alerts(plugins):
    """Return cached alert strings. Never blocks — returns stale or empty if not yet fetched."""
    return _ALERT_CACHE["alerts"]


def refresh_health_alerts(host=None, port=None):
    """Force-refresh health alerts (call from background thread)."""
    alerts = _fetch_health_alerts(host, port)
    alerts.extend(_fetch_api_health_alerts())
    alerts.extend(_fetch_ssl_expiry_alerts())
    _ALERT_CACHE["alerts"] = alerts
    _ALERT_CACHE["timestamp"] = time.time()


def _fetch_health_alerts(host=None, port=None):
    """Do the actual SSH call to check health issues."""
    alerts = []

    if not host:
        return alerts

    try:
        import subprocess as _sp

        # Single SSH call to check health issues (with timeout to avoid hangs)
        cmd = (
            "docker ps --filter health=unhealthy --format '{{.Names}}' 2>/dev/null;"
            "echo '---SEP---';"
            "df -h /mnt/user 2>/dev/null | tail -1;"
            "echo '---SEP---';"
            "docker stats --no-stream --format '{{.Name}}\\t{{.CPUPerc}}' 2>/dev/null"
        )
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if port:
            ssh_cmd.extend(["-p", str(port)])
        ssh_cmd.extend([host, cmd])
        result = _sp.run(
            ssh_cmd,
            capture_output=True, text=True, timeout=15,
            stdin=_sp.DEVNULL,
        )
        if result.returncode != 0:
            return alerts

        parts = result.stdout.split("---SEP---")
        if len(parts) < 3:
            return alerts

        # Unhealthy containers
        unhealthy = [n.strip() for n in parts[0].strip().split("\n") if n.strip()]
        if unhealthy:
            names = ", ".join(unhealthy[:3])
            if len(unhealthy) > 3:
                names += f" +{len(unhealthy) - 3} more"
            alerts.append(f"{C.RED}Unhealthy:{C.RESET} {names}")

        # Low disk space
        df_line = parts[1].strip()
        df_parts = df_line.split()
        if len(df_parts) >= 5:
            pct_str = df_parts[4].rstrip("%")
            try:
                pct = int(pct_str)
                if pct > 90:
                    alerts.append(f"{C.RED}Disk: {pct}% full{C.RESET} ({df_parts[3]} free)")
                elif pct > 80:
                    alerts.append(f"{C.YELLOW}Disk: {pct}% full{C.RESET} ({df_parts[3]} free)")
            except ValueError:
                pass

        # High CPU containers (>50%)
        high_cpu = []
        for line in parts[2].strip().split("\n"):
            if not line.strip():
                continue
            cpu_parts = line.split("\t")
            if len(cpu_parts) >= 2:
                name = cpu_parts[0]
                cpu_str = cpu_parts[1].rstrip("%")
                try:
                    cpu = float(cpu_str)
                    if cpu > 50:
                        high_cpu.append(f"{name} ({cpu:.0f}%)")
                except ValueError:
                    pass
        if high_cpu:
            names = ", ".join(high_cpu[:3])
            alerts.append(f"{C.YELLOW}High CPU:{C.RESET} {names}")

    except (_sp.TimeoutExpired, Exception):
        pass

    return alerts


# Service URL config keys to check for API reachability
_SERVICE_URL_KEYS = [
    ("homeassistant_url", "Home Assistant"),
    ("proxmox_url", "Proxmox"),
    ("forgejo_url", "Forgejo"),
    ("unifi_url", "UniFi"),
    ("opnsense_url", "OPNsense"),
    ("uptimekuma_url", "Uptime Kuma"),
    ("npm_url", "Nginx Proxy Manager"),
    ("immich_url", "Immich"),
    ("syncthing_url", "Syncthing"),
    ("plex_url", "Plex"),
    ("jellyfin_url", "Jellyfin"),
    ("sonarr_url", "Sonarr"),
    ("radarr_url", "Radarr"),
    ("lidarr_url", "Lidarr"),
    ("sabnzbd_url", "SABnzbd"),
    ("deluge_url", "Deluge"),
]


def _fetch_api_health_alerts():
    """Check configured service URLs are reachable. Only alert on connection failures."""
    alerts = []
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for key, label in _SERVICE_URL_KEYS:
        url = CFG.get(key, "")
        if not url:
            continue
        try:
            req = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(req, timeout=5, context=ctx)
        except urllib.error.HTTPError:
            pass  # Any HTTP response means the service is up
        except Exception:
            alerts.append(f"{C.RED}{label} unreachable{C.RESET}")

    return alerts


def _fetch_ssl_expiry_alerts():
    """Check SSL cert expiry for HTTPS service URLs. Alert if expiring within 14 days."""
    alerts = []

    for key, label in _SERVICE_URL_KEYS:
        url = CFG.get(key, "")
        if not url or not url.startswith("https://"):
            continue
        try:
            # Extract hostname and port from URL
            host_part = url.split("://", 1)[1].split("/")[0]
            if ":" in host_part:
                hostname, port_str = host_part.rsplit(":", 1)
                port = int(port_str)
            else:
                hostname = host_part
                port = 443

            ctx = ssl.create_default_context()
            with socket.create_connection((hostname, port), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
                    if not cert:
                        continue
                    not_after = cert.get("notAfter", "")
                    if not not_after:
                        continue
                    # Parse SSL date format: 'Mon DD HH:MM:SS YYYY GMT'
                    expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                    expiry = expiry.replace(tzinfo=timezone.utc)
                    days_left = (expiry - datetime.now(timezone.utc)).days
                    if days_left < 0:
                        alerts.append(f"{C.RED}{label} SSL expired{C.RESET}")
                    elif days_left <= 14:
                        alerts.append(
                            f"{C.YELLOW}{label} SSL expires in {days_left}d{C.RESET}"
                        )
        except Exception:
            # Self-signed certs or connection issues — skip silently
            pass

    return alerts
