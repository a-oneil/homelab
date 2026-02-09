"""SSH, rsync, and file transfer helpers."""

import re
import subprocess

from homelab.config import CFG
from homelab.ui import info, error, warn, confirm, check_tool


def get_host():
    return CFG["unraid_ssh_host"]


def get_base():
    return CFG["unraid_remote_base"]


def ssh_run(command, capture=True, background=False):
    if background:
        # Non-interactive: prevent SSH from touching /dev/tty (password
        # prompts, host-key verification) which corrupts prompt_toolkit.
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            get_host(), command,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    cmd = ["ssh", get_host(), command]
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(cmd)


def _check_disk_space(remote_path):
    """Warn if target share is low on space. Returns True to proceed."""
    threshold = CFG.get("disk_space_warn_gb", 5)
    if threshold <= 0:
        return True
    result = ssh_run(f"df -B1 '{remote_path}' 2>/dev/null | tail -1")
    if result.returncode != 0 or not result.stdout.strip():
        return True
    parts = result.stdout.strip().split()
    if len(parts) < 4:
        return True
    try:
        avail_bytes = int(parts[3])
        avail_gb = avail_bytes / (1024 ** 3)
        if avail_gb < threshold:
            warn(
                f"Only {avail_gb:.1f} GB free on this share "
                f"(threshold: {threshold} GB)."
            )
            return confirm("Continue anyway?", default_yes=False)
    except (ValueError, IndexError):
        pass
    return True


def rsync_transfer(source, dest_spec, is_dir=False):
    if CFG.get("dry_run"):
        info(f"[DRY RUN] Would transfer: {source} → {dest_spec}")

        class FakeResult:
            returncode = 0
            stdout = ""
        return FakeResult()

    if check_tool("rsync"):
        cmd = ["rsync", "-ah", "--progress", "--stats", "-e", "ssh"]
        cmd.extend([source, dest_spec])
    else:
        cmd = ["scp"]
        if is_dir:
            cmd.append("-r")
        cmd.extend([source, dest_spec])

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    output_lines = []
    for line in proc.stdout:
        print(line, end="")
        output_lines.append(line)
    proc.wait()

    if proc.returncode == 0 and check_tool("rsync"):
        full = "".join(output_lines)
        m = re.search(
            r"sent\s+([\d,]+)\s+bytes.*?([\d,.]+)\s+bytes/sec",
            full,
        )
        if m:
            sent = int(m.group(1).replace(",", ""))
            speed = float(m.group(2).replace(",", ""))
            if sent > 1_000_000:
                sz = f"{sent / 1_000_000:.1f} MB"
            else:
                sz = f"{sent / 1_000:.1f} KB"
            if speed > 1_000_000:
                spd = f"{speed / 1_000_000:.1f} MB/s"
            else:
                spd = f"{speed / 1_000:.1f} KB/s"
            info(f"Transfer: {sz} at {spd}")

    class Result:
        pass
    r = Result()
    r.returncode = proc.returncode
    r.stdout = "".join(output_lines)
    return r


def list_remote_dirs(remote_path):
    result = ssh_run(f"find '{remote_path}' -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort")
    if result.returncode != 0:
        error(f"Error listing remote directories: {result.stderr.strip()}")
        return []
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


def list_remote_items(remote_path):
    result = ssh_run(f"ls -lhA --group-directories-first '{remote_path}' 2>/dev/null")
    if result.returncode != 0:
        error(f"Error listing remote path: {result.stderr.strip()}")
        return [], []
    lines = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
    if lines and lines[0].startswith("total"):
        lines = lines[1:]
    dirs = []
    files = []
    for line in lines:
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        perms, _, _, _, size, _, _, _, name = parts
        if perms.startswith("d"):
            dirs.append((name, size, "dir"))
        else:
            files.append((name, size, "file"))
    return dirs, files


def format_item(name, size, item_type):
    if item_type == "dir":
        return f"[DIR]  {name}/"
    return f"       {name}  ({size})"
