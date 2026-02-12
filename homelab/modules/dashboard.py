"""Status Dashboard — single-screen overview of all configured plugins."""

from homelab.ui import C, pick_option


def status_dashboard(plugins):
    """Show a unified dashboard pulling widgets from all configured plugins."""
    while True:
        lines = []
        lines.append(f"\n  {C.ACCENT}{C.BOLD}╔══════════════════════════════════╗{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}║       STATUS DASHBOARD           ║{C.RESET}")
        lines.append(f"  {C.ACCENT}{C.BOLD}╚══════════════════════════════════╝{C.RESET}")
        lines.append("")

        any_widget = False
        for plugin in plugins:
            if not plugin.is_configured():
                continue

            widgets = plugin.get_dashboard_widgets()
            if widgets:
                for widget in widgets:
                    any_widget = True
                    title = widget.get("title", plugin.name)
                    wlines = widget.get("lines", [])
                    lines.append(f"  {C.ACCENT}┌─{C.RESET} {C.BOLD}{title}{C.RESET}")
                    for wl in wlines:
                        lines.append(f"  {C.ACCENT}│{C.RESET}  {wl}")
                    lines.append(f"  {C.ACCENT}└─{C.RESET}")
                    lines.append("")
            else:
                # Fall back to header stats for plugins without widgets
                stats = plugin.get_header_stats()
                if stats:
                    any_widget = True
                    lines.append(f"  {C.ACCENT}┌─{C.RESET} {C.BOLD}{plugin.name}{C.RESET}")
                    lines.append(f"  {C.ACCENT}│{C.RESET}  {stats}")
                    lines.append(f"  {C.ACCENT}└─{C.RESET}")
                    lines.append("")

            # Show health alerts inline
            alerts = plugin.get_health_alerts()
            if alerts:
                any_widget = True
                for alert in alerts:
                    lines.append(f"  {C.BOLD}!{C.RESET} {alert}")

        if not any_widget:
            lines.append(f"  {C.DIM}No plugins configured. Go to Settings to add services.{C.RESET}")

        lines.append("")
        header = "\n".join(lines)
        idx = pick_option("Dashboard:", ["Refresh", "← Back"], header=header)
        if idx == 1:
            return
