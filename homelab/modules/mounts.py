"""Mount Monitor — display filesystem mount points with usage bars."""

import subprocess

from homelab.modules.transport import ssh_run
from homelab.modules.auditlog import log_action
from homelab.ui import C, pick_option, bar_chart, error

_SKIP_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "efivarfs", "devpts",
            "sysfs", "proc", "cgroup", "cgroup2", "securityfs", "debugfs",
            "pstore", "bpf", "tracefs", "hugetlbfs", "mqueue", "configfs",
            "fusectl", "ramfs", "nsfs", "fuse.lxcfs"}


def show_mounts(host, port=None):
    """Display mount points with filesystem type and usage."""
    while True:
        result = ssh_run("df -hT 2>/dev/null", host=host, port=port)
        if result.returncode != 0 or not result.stdout.strip():
            error("Failed to get mount information.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        lines = result.stdout.strip().split("\n")
        rows = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 7:
                continue
            fs_type = parts[1]
            if fs_type in _SKIP_FS:
                continue
            size = parts[2]
            used = parts[3]
            pct_str = parts[5].rstrip("%")
            mount_point = " ".join(parts[6:])

            try:
                pct = int(pct_str)
                chart = bar_chart(pct, 100, width=20)
            except ValueError:
                chart = ""

            rows.append(f"  {mount_point:<30} {fs_type:<10} {used:>6}/{size:<6} {chart}")

        if not rows:
            error("No real filesystems found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        header_lines = [
            f"  {C.BOLD}Mount Points{C.RESET}",
            "",
            f"  {C.DIM}{'Mount':<30} {'Type':<10} {'Used/Total':>13} {'Usage'}{C.RESET}",
            f"  {'─' * 75}",
        ] + rows + [""]
        header = "\n".join(header_lines)

        idx = pick_option("Mount Monitor:", [
            "Edit /etc/fstab   — edit filesystem table (sudo)",
            "← Back",
        ], header=header)
        if idx == 1:
            return
        elif idx == 0:
            _edit_fstab(host, port)


def _edit_fstab(host, port=None):
    """Open /etc/fstab in an editor via sudo over SSH TTY."""
    log_action("Edit fstab", host)
    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.extend([host, "sudo nano /etc/fstab"])
    subprocess.run(cmd)
