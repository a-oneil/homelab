"""Container Update Checker — compare running Docker images against registry."""

import subprocess

from homelab.ui import C, pick_option, info, error, warn


def check_container_updates():
    """Check all running Docker containers for available image updates."""
    from homelab.transport import get_host
    host = get_host()
    if not host:
        error("No SSH host configured. Configure Unraid SSH Host in Settings.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        info("Checking container images for updates (this may take a minute)...")
        print()

        try:
            # Get running containers with their image info
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
                 "docker ps --format '{{.Names}}\\t{{.Image}}\\t{{.ID}}' 2>/dev/null"],
                capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            error("Timed out connecting to server.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        if result.returncode != 0 or not result.stdout.strip():
            error("Could not list containers.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        containers = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 3:
                containers.append({
                    "name": parts[0],
                    "image": parts[1],
                    "id": parts[2][:12],
                })

        if not containers:
            warn("No running containers found.")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        # For each container, compare local digest against remote
        # Use a single SSH call with a script to check all at once
        image_list = " ".join(f"'{c['image']}'" for c in containers)
        check_script = (
            f"for img in {image_list}; do "
            "local_digest=$(docker image inspect \"$img\" --format '{{.Id}}' 2>/dev/null | cut -c1-19); "
            "remote_digest=$(docker pull \"$img\" 2>/dev/null | grep 'Digest:' | awk '{print $2}' | cut -c1-19); "
            "if [ -z \"$remote_digest\" ]; then "
            "  echo \"${img}\\tSKIP\\t${local_digest}\"; "
            "elif [ \"$local_digest\" != \"$remote_digest\" ]; then "
            "  echo \"${img}\\tUPDATE\\t${local_digest}\\t${remote_digest}\"; "
            "else "
            "  echo \"${img}\\tOK\\t${local_digest}\"; "
            "fi; "
            "done"
        )

        info(f"Pulling latest digests for {len(containers)} images...")
        try:
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, check_script],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            error("Update check timed out (some images may be very large).")
            input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
            return

        # Parse results
        updates_available = []
        up_to_date = []
        skipped = []

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            image = parts[0] if parts else "?"
            status = parts[1] if len(parts) > 1 else "?"

            # Find container name for this image
            cname = image
            for c in containers:
                if c["image"] == image:
                    cname = c["name"]
                    break

            if status == "UPDATE":
                updates_available.append(cname)
            elif status == "OK":
                up_to_date.append(cname)
            else:
                skipped.append(cname)

        # Display results
        lines = []
        lines.append(f"\n  {C.ACCENT}{C.BOLD}╔══════════════════════════════════╗{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}║     CONTAINER UPDATE CHECK       ║{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}╚══════════════════════════════════╝{C.RESET}")
        lines.append("")

        if updates_available:
            lines.append(f"  {C.YELLOW}{C.BOLD}Updates Available ({len(updates_available)}):{C.RESET}")
            for name in sorted(updates_available):
                lines.append(f"    {C.YELLOW}↑{C.RESET} {name}")
            lines.append("")

        if up_to_date:
            lines.append(f"  {C.GREEN}{C.BOLD}Up to Date ({len(up_to_date)}):{C.RESET}")
            for name in sorted(up_to_date):
                lines.append(f"    {C.GREEN}✓{C.RESET} {name}")
            lines.append("")

        if skipped:
            lines.append(f"  {C.DIM}Skipped ({len(skipped)}): {', '.join(sorted(skipped))}{C.RESET}")
            lines.append("")

        summary = (
            f"  {C.BOLD}Summary:{C.RESET} "
            f"{C.YELLOW}{len(updates_available)} updates{C.RESET}  "
            f"{C.GREEN}{len(up_to_date)} current{C.RESET}  "
            f"{C.DIM}{len(skipped)} skipped{C.RESET}"
        )
        lines.append(summary)
        lines.append("")

        header = "\n".join(lines)
        idx = pick_option("", ["Refresh", "← Back"], header=header)
        if idx == 1:
            return
