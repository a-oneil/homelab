"""SSH, rsync, and file transfer helpers."""

import os
import re
import subprocess
import sys
import tty
import termios

from homelab.config import CFG
from homelab.ui import C, info, error, warn, confirm, check_tool, prompt_text, clear_screen


def ssh_run(command, capture=True, background=False, host=None, port=None):
    target = host
    if background:
        # Non-interactive: prevent SSH from touching /dev/tty (password
        # prompts, host-key verification) which corrupts prompt_toolkit.
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
        ]
        if port:
            cmd.extend(["-p", str(port)])
        cmd.extend([target, command])
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL,
        )
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if port:
        cmd.extend(["-p", str(port)])
    cmd.extend([target, command])
    if capture:
        # Close stdin so background SSH won't steal terminal input from
        # prompt_toolkit (e.g. CPR responses), which causes typing lag.
        return subprocess.run(
            cmd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
        )
    return subprocess.run(cmd)


def _check_disk_space(remote_path, host=None):
    """Warn if target share is low on space. Returns True to proceed."""
    threshold = CFG.get("disk_space_warn_gb", 5)
    if threshold <= 0:
        return True
    result = ssh_run(f"df -B1 '{remote_path}' 2>/dev/null | tail -1", host=host)
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


RSYNC_OPTIONS = [
    ("--ignore-existing", "Skip existing", "don't overwrite files that already exist on destination"),
    ("--update", "Skip newer", "skip files that are newer on the destination"),
    ("--append-verify", "Resume partial", "resume incomplete files + verify with checksum"),
    ("--delete", "Delete extra", "remove files on destination not in source"),
    ("--compress", "Compress", "compress data during transfer (slow network)"),
    ("--bwlimit", "Bandwidth limit", "cap transfer speed (KiB/s)"),
    ("--dry-run", "Dry run", "show what would be transferred without doing it"),
    ("--checksum", "Checksum", "skip based on checksum, not mod-time & size"),
]


def _read_key():
    """Read a single keypress (or escape sequence) from stdin."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                if ch3 == 'A':
                    return 'up'
                if ch3 == 'B':
                    return 'down'
            return 'escape'
        if ch in ('\r', '\n'):
            return 'enter'
        if ch == ' ':
            return 'space'
        if ch == '\x07':  # Ctrl-G
            return 'cancel'
        if ch == '\x03':  # Ctrl-C
            return 'cancel'
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_rsync_picker(cursor, selected, total_lines, source="", dest="", port=None):
    """Render the rsync options picker with live command preview."""
    ssh_part = f"-e 'ssh -p {port}' " if port else ""
    extra = " ".join(RSYNC_OPTIONS[i][0] for i in sorted(selected))
    parts = ["rsync -ah --progress --stats", ssh_part + extra, source, dest]
    cmd_preview = " ".join(p for p in parts if p)

    lines = []
    lines.append(f"  {C.DIM}${C.RESET} {C.ACCENT}{cmd_preview}{C.RESET}")
    lines.append(f"  {C.BOLD}Transfer options{C.RESET} {C.DIM}(↑↓ navigate, Space toggle, Enter confirm, Ctrl-G skip){C.RESET}")

    for i, (flag, name, desc) in enumerate(RSYNC_OPTIONS):
        marker = f"{C.GREEN}●{C.RESET}" if i in selected else f"{C.DIM}○{C.RESET}"
        pointer = f"{C.ACCENT}»{C.RESET}" if i == cursor else " "
        lines.append(f"  {pointer} {marker} {C.ACCENT}{flag:20}{C.RESET} {name:16} {C.DIM}{desc}{C.RESET}")

    # Move cursor up to overwrite previous render
    if total_lines > 0:
        sys.stdout.write(f"\033[{total_lines}A")

    for ln in lines:
        sys.stdout.write(f"\033[2K{ln}\n")
    sys.stdout.flush()

    return len(lines)


def pick_rsync_options(source="", dest="", port=None):
    """Interactive rsync option picker with live command preview."""
    clear_screen()
    cursor = 0
    selected = set()
    total_lines = 0

    total_lines = _render_rsync_picker(cursor, selected, total_lines, source, dest, port)

    while True:
        key = _read_key()
        if key == 'up':
            cursor = (cursor - 1) % len(RSYNC_OPTIONS)
        elif key == 'down':
            cursor = (cursor + 1) % len(RSYNC_OPTIONS)
        elif key == 'space':
            if cursor in selected:
                selected.discard(cursor)
            else:
                selected.add(cursor)
        elif key == 'enter':
            break
        elif key == 'cancel':
            # Clear and return empty
            sys.stdout.write(f"\033[{total_lines}A\033[J")
            sys.stdout.flush()
            return []
        else:
            continue

        total_lines = _render_rsync_picker(cursor, selected, total_lines, source, dest, port)

    # Clear picker display
    sys.stdout.write(f"\033[{total_lines}A\033[J")
    sys.stdout.flush()

    if not selected:
        return []

    extra = []
    for i in sorted(selected):
        flag = RSYNC_OPTIONS[i][0]
        if flag == "--bwlimit":
            limit = prompt_text("Bandwidth limit (KiB/s):", default="5000")
            if limit and limit.isdigit():
                extra.append(f"--bwlimit={limit}")
            else:
                info("Skipped bandwidth limit (invalid value).")
        else:
            extra.append(flag)
    return extra


# Regex for rsync --progress output lines
_RSYNC_PROGRESS_RE = re.compile(
    r'\s+([\d,]+)\s+(\d+)%\s+([\d.]+\S+/s)\s+(\S+)'
    r'(?:\s+\(x(?:fer|fr)#(\d+),\s*to-ch(?:eck|k)=(\d+)/(\d+)\))?'
)

_STATS_PREFIXES = (
    "Number of ", "Total file size", "Total transferred",
    "Literal data", "Matched data", "File list",
    "sent ", "total size",
)


_MAX_RECENT = 8


def _render_transfer_progress(file_name, file_pct, speed,
                              checked, total, xferred,
                              completed_files, lines_rendered):
    """Render progress with scrolling completed file list. Returns line count."""
    try:
        term_w = os.get_terminal_size().columns
    except (ValueError, OSError):
        term_w = 80

    lines = []

    # Line 1: overall progress bar
    bar_w = 30
    if total > 0:
        # Interpolate current file progress for smoother bar movement
        file_frac = 0
        try:
            if file_pct:
                file_frac = int(file_pct) / 100
        except (ValueError, TypeError):
            pass
        effective = checked + file_frac
        pct = min(effective / total, 1.0)
        filled = int(bar_w * pct)
        bar = f"{C.ACCENT}{'█' * filled}{C.DIM}{'░' * (bar_w - filled)}{C.RESET}"
        remaining = total - checked
        lines.append(f"  [{bar}] {pct:>4.0%}"
                     f" | {speed:>12}"
                     f" | {xferred} sent, {remaining} remaining")
    else:
        bar = f"{C.DIM}{'░' * bar_w}{C.RESET}"
        lines.append(f"  [{bar}]  ... | {'scanning...':>12}")

    # Recently completed files
    max_name = term_w - 8
    for fname in completed_files[-_MAX_RECENT:]:
        name = fname
        if len(name) > max_name > 4:
            name = "…" + name[-(max_name - 1):]
        lines.append(f"  {C.GREEN}✓{C.RESET} {C.DIM}{name}{C.RESET}")

    # Current file + per-file %
    max_cur = term_w - 15
    name = file_name
    if len(name) > max_cur > 4:
        name = "…" + name[-(max_cur - 1):]
    cur = f"  {C.ACCENT}→{C.RESET} {name}"
    if file_pct and name:
        cur += f"  {C.DIM}{file_pct}%{C.RESET}"
    lines.append(cur)

    # Overwrite previous render
    if lines_rendered > 0:
        sys.stdout.write(f"\033[{lines_rendered}A")

    for ln in lines:
        sys.stdout.write(f"\033[2K{ln}\n")
    sys.stdout.flush()

    return len(lines)


def _display_rsync_progress(proc, output_lines):
    """Parse rsync --progress output and render a live progress display."""
    current_file = ""
    total = 0
    checked = 0
    xferred = 0
    speed = ""
    file_pct = ""
    lines_rendered = 0
    in_stats = False
    error_lines = []
    completed_files = []

    # Use readline for real-time output (default iterator buffers in binary mode)
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", errors="replace")
        output_lines.append(line)

        # rsync may embed \r for in-place updates within a single \n line;
        # only process the last segment (most recent update)
        segment = line.rstrip("\n").rsplit("\r", 1)[-1]
        stripped = segment.rstrip()

        if not stripped:
            continue

        # Detect end-of-transfer stats section
        trimmed = stripped.lstrip()
        if any(trimmed.startswith(p) for p in _STATS_PREFIXES):
            in_stats = True
        if in_stats:
            continue

        # Try to parse as a progress line
        m = _RSYNC_PROGRESS_RE.match(stripped)
        if m:
            file_pct = m.group(2)
            speed = m.group(3)
            if m.group(5):
                new_xferred = int(m.group(5))
                remaining_rsync = int(m.group(6))
                new_total = int(m.group(7))
                total = max(total, new_total)
                from_tocheck = new_total - remaining_rsync
                # Advance checked by at least 1 per transferred file
                # (handles incremental recursion where total grows and
                # to-check progress appears stuck)
                xfer_delta = max(0, new_xferred - xferred)
                checked = min(max(checked + xfer_delta, from_tocheck), total)
                xferred = new_xferred
                # File just completed transfer
                if current_file:
                    completed_files.append(current_file)
        elif not stripped.startswith(" "):
            trimmed = stripped.strip()
            # Detect error lines from rsync/ssh
            if (trimmed.startswith("rsync:") or trimmed.startswith("rsync error")
                    or trimmed.startswith("ssh:") or trimmed.startswith("@ERROR")):
                error_lines.append(trimmed)
                continue
            # Filename line
            current_file = trimmed
            file_pct = ""
        else:
            continue

        lines_rendered = _render_transfer_progress(
            current_file, file_pct, speed,
            checked, total, xferred,
            completed_files, lines_rendered,
        )

    # Clear progress display
    if lines_rendered > 0:
        sys.stdout.write(f"\033[{lines_rendered}A\033[J")
        sys.stdout.flush()

    # Show any rsync errors that were collected
    for err in error_lines:
        error(err[:200])


def rsync_transfer(source, dest_spec, is_dir=False, port=None, extra_args=None):
    if CFG.get("dry_run"):
        info(f"[DRY RUN] Would transfer: {source} → {dest_spec}")

        class FakeResult:
            returncode = 0
            stdout = ""
        return FakeResult()

    use_rsync = check_tool("rsync")
    if use_rsync:
        ssh_cmd = "ssh -p " + str(port) if port else "ssh"
        cmd = ["rsync", "-ah", "--progress", "--stats", "-e", ssh_cmd]
        if extra_args:
            cmd.extend(extra_args)
        cmd.extend([source, dest_spec])
    else:
        cmd = ["scp"]
        if port:
            cmd.extend(["-P", str(port)])
        if is_dir:
            cmd.append("-r")
        cmd.extend([source, dest_spec])

    info(f"{source}")
    info(f"→ {dest_spec}")
    if extra_args:
        info(f"Options: {' '.join(extra_args)}")
    print()

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    output_lines = []

    if use_rsync:
        _display_rsync_progress(proc, output_lines)
    else:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            print(line, end="")
            output_lines.append(line)
    proc.wait()

    if proc.returncode == 0 and use_rsync:
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


def list_remote_dirs(remote_path, host=None, port=None):
    result = ssh_run(f"find '{remote_path}' -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort", host=host, port=port)
    if result.returncode != 0:
        error(f"Error listing remote directories: {result.stderr.strip()}")
        return []
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


def list_remote_items(remote_path, host=None, port=None):
    result = ssh_run(f"ls -lhA --group-directories-first '{remote_path}' 2>/dev/null", host=host, port=port)
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
