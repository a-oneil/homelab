"""SSH, rsync, and file transfer helpers."""

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
    ("--remove-source-files", "Remove source", "delete source files after successful transfer (move)"),
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


def pick_rsync_options(source="", dest="", port=None, preselected=None):
    """Interactive rsync option picker with live command preview.

    Args:
        preselected: set of flag strings to pre-select (e.g. {"--remove-source-files"}).
    """
    clear_screen()
    cursor = 0
    selected = set()
    if preselected:
        for i, (flag, _, _) in enumerate(RSYNC_OPTIONS):
            if flag in preselected:
                selected.add(i)
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


_STATS_PREFIXES = (
    "Number of ", "Total file size", "Total transferred",
    "Literal data", "Matched data", "File list",
    "sent ", "total size",
)


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

    is_dry_run = extra_args and "--dry-run" in extra_args

    if is_dry_run:
        # Capture output for dry-run file listing
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        output_lines = []
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            output_lines.append(line)
        proc.wait()

        if use_rsync:
            files_listed = []
            for line in output_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                trimmed = stripped.lstrip()
                if any(trimmed.startswith(p) for p in _STATS_PREFIXES):
                    continue
                if trimmed.startswith("created directory"):
                    continue
                files_listed.append(stripped)
            if files_listed:
                print(f"  {C.BOLD}Files that would be transferred:{C.RESET}")
                for f in files_listed:
                    print(f"    {C.ACCENT}{f}{C.RESET}")
                print()
            else:
                info("No files would be transferred (all up to date).")
    else:
        # Show raw rsync/scp output directly
        proc = subprocess.run(cmd)

    class Result:
        pass
    r = Result()
    r.returncode = proc.returncode
    r.stdout = ""
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
