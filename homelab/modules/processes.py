"""Process Explorer — view and manage running processes."""

from homelab.modules.transport import ssh_run
from homelab.modules.auditlog import log_action
from homelab.ui import C, pick_option, scrollable_list, confirm, prompt_text, success, error


def show_processes(host, port=None):
    """Interactive process viewer with sort options."""
    while True:
        idx = pick_option("Process Explorer:", [
            "Top by CPU        — highest CPU usage",
            "Top by Memory     — highest memory usage",
            "All processes     — sorted by PID",
            "Kill a process    — send signal by PID",
            "← Back",
        ])
        if idx == 4:
            return
        elif idx == 3:
            _kill_process(host, port)
        else:
            sort_flags = ["--sort=-%cpu", "--sort=-%mem", "--sort=pid"]
            _list_processes(host, port, sort_flags[idx])


def _list_processes(host, port, sort_flag):
    """Fetch and display top 50 processes."""
    result = ssh_run(
        f"ps aux {sort_flag} 2>/dev/null | head -51",
        host=host, port=port,
    )
    if result.returncode != 0 or not result.stdout.strip():
        error("Failed to get process list.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    lines = result.stdout.strip().split("\n")
    rows = []
    for i, line in enumerate(lines):
        if i == 0:
            # Header line
            rows.append(f"  {C.DIM}{line}{C.RESET}")
            continue
        parts = line.split(None, 10)
        if len(parts) < 11:
            rows.append(f"  {line}")
            continue

        pid = parts[1]
        cpu = parts[2]
        mem = parts[3]
        command = parts[10][:60]

        try:
            cpu_val = float(cpu)
            if cpu_val > 50:
                color = C.RED
            elif cpu_val > 20:
                color = C.YELLOW
            else:
                color = C.GREEN
        except ValueError:
            color = C.RESET

        rows.append(
            f"  {pid:>7}  {color}{cpu:>5}%{C.RESET}  {mem:>5}%  {command}"
        )

    header = f"  {C.DIM}{'PID':>7}  {'CPU':>6}  {'MEM':>6}  {'COMMAND'}{C.RESET}"
    scrollable_list("Processes", rows[1:], header_line=header)


def _kill_process(host, port):
    """Kill a process by PID."""
    pid = prompt_text("Enter PID to kill:")
    if not pid or not pid.strip().isdigit():
        return

    pid = pid.strip()
    sig_idx = pick_option(f"Signal for PID {pid}:", [
        "SIGTERM (15)      — graceful shutdown",
        "SIGKILL (9)       — force kill",
        "← Cancel",
    ])
    if sig_idx == 2:
        return

    signal = "15" if sig_idx == 0 else "9"
    sig_name = "SIGTERM" if sig_idx == 0 else "SIGKILL"

    if confirm(f"Send {sig_name} to PID {pid}?", default_yes=False):
        result = ssh_run(f"kill -{signal} {pid} 2>&1", host=host, port=port)
        if result.returncode == 0:
            log_action("Kill Process", f"PID {pid} ({sig_name}) on {host}")
            success(f"Sent {sig_name} to PID {pid}")
        else:
            error(f"Failed: {result.stdout.strip() or result.stderr.strip()}")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
