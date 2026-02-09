"""Health Monitor — check for unhealthy containers, high CPU, low disk."""

import time

from homelab.config import CFG
from homelab.ui import C

_ALERT_CACHE = {"timestamp": 0, "alerts": []}
_CACHE_TTL = 300  # 5 minutes


def get_health_alerts(plugins):
    """Return cached alert strings. Never blocks — returns stale or empty if not yet fetched."""
    return _ALERT_CACHE["alerts"]


def refresh_health_alerts():
    """Force-refresh health alerts (call from background thread)."""
    _ALERT_CACHE["alerts"] = _fetch_health_alerts()
    _ALERT_CACHE["timestamp"] = time.time()


def _fetch_health_alerts():
    """Do the actual SSH call to check health issues."""
    alerts = []

    if not CFG.get("unraid_ssh_host"):
        return alerts

    try:
        import subprocess as _sp
        from homelab.transport import get_host

        # Single SSH call to check health issues (with timeout to avoid hangs)
        cmd = (
            "docker ps --filter health=unhealthy --format '{{.Names}}' 2>/dev/null;"
            "echo '---SEP---';"
            "df -h /mnt/user 2>/dev/null | tail -1;"
            "echo '---SEP---';"
            "docker stats --no-stream --format '{{.Name}}\\t{{.CPUPerc}}' 2>/dev/null"
        )
        result = _sp.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             get_host(), cmd],
            capture_output=True, text=True, timeout=15,
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
