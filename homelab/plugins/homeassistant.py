"""Home Assistant plugin — browse entities and call services."""

import json
import subprocess
import time
import urllib.request

from homelab.modules.auditlog import log_action
from homelab.config import CFG, save_config
from homelab.plugins import Plugin
from homelab.ui import C, pick_option, scrollable_list, confirm, prompt_text, info, success, error, warn

_HEADER_CACHE = {"timestamp": 0, "stats": ""}
_CACHE_TTL = 300


def _api(endpoint, method="GET", data=None, quiet=False):
    """Make an authenticated API call to Home Assistant."""
    base = CFG.get("homeassistant_url", "").rstrip("/")
    token = CFG.get("homeassistant_token", "")
    if not base or not token:
        return None

    url = f"{base}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if data:
        payload = json.dumps(data).encode()
    else:
        payload = None

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        if not quiet:
            error(f"Home Assistant API error: {e}")
        return None


class HomeAssistantPlugin(Plugin):
    name = "Home Assistant"
    key = "homeassistant"

    def is_configured(self):
        return bool(CFG.get("homeassistant_url") and CFG.get("homeassistant_token"))

    def get_config_fields(self):
        return [
            ("homeassistant_url", "HA URL", "e.g. http://192.168.1.100:8123", False),
            ("homeassistant_token", "HA Token", "Long-lived access token", True),
            ("homeassistant_ssh_host", "HA SSH Host", "e.g. root@192.168.1.100", False),
            ("homeassistant_ssh_port", "HA SSH Port", "e.g. 22222 (leave blank for 22)", False),
        ]

    def get_header_stats(self):
        if time.time() - _HEADER_CACHE["timestamp"] > _CACHE_TTL:
            _fetch_stats()
        return _HEADER_CACHE.get("stats") or None

    def get_menu_items(self):
        return [
            ("Home Assistant       — entities and automations", ha_menu),
        ]

    def get_actions(self):
        return {
            "HA Browse Entities": ("ha_browse", _browse_entities),
            "HA Call Service": ("ha_service", _call_service),
            "HA Dashboard": ("ha_dashboard", _dashboard_view),
            "HA Automations": ("ha_automations", _automations_and_scenes),
            "HA System Info": ("ha_system", _system_info),
        }

    def resolve_favorite(self, fav):
        if fav.get("type") == "ha_entity":
            entity_id = fav["id"]
            return lambda eid=entity_id: _show_entity_by_id(eid)
        elif fav.get("type") == "ha_service_call":
            entity_id = fav["id"]
            service = fav.get("service", "toggle")
            return lambda eid=entity_id, svc=service: _resolve_service_call(eid, svc)


def _fetch_stats():
    states = _api("/api/states")
    if states and isinstance(states, list):
        _HEADER_CACHE["stats"] = f"HA: {len(states)} entities"
    _HEADER_CACHE["timestamp"] = time.time()


def ha_menu():
    ssh_host = CFG.get("homeassistant_ssh_host", "")
    while True:
        items = [
            "Automations & Scenes  — manage automations and activate scenes",
            "Browse Entities       — search and view entity states",
            "Call Service          — perform action on entity",
            "Dashboard View        — quick glance at key entities",
            "Log Viewer            — recent state changes for an entity",
            "System Info           — HA config, version, integrations",
            "Add-ons               — manage HA OS add-ons",
        ]
        if ssh_host:
            items.append("SSH Shell             — open a terminal session")
        items.extend([
            "───────────────",
            "★ Add to Favorites   — pin an action to the main menu",
            "← Back",
        ])
        idx = pick_option("Home Assistant:", items)
        label = items[idx]
        if label == "← Back":
            return
        elif label.startswith("───"):
            continue
        elif label.startswith("★"):
            from homelab.plugins import add_plugin_favorite
            add_plugin_favorite(HomeAssistantPlugin())
        elif label.startswith("Automations"):
            _automations_and_scenes()
        elif label.startswith("Browse"):
            _browse_entities()
        elif label.startswith("Call Service"):
            _call_service()
        elif label.startswith("Dashboard"):
            _dashboard_view()
        elif label.startswith("Log Viewer"):
            _log_viewer()
        elif label.startswith("System Info"):
            _system_info()
        elif label.startswith("Add-ons"):
            _addon_management()
        elif label.startswith("SSH"):
            _ha_ssh_shell()


def _ha_ssh_shell():
    """Open an interactive SSH session to the Home Assistant host."""
    host = CFG.get("homeassistant_ssh_host", "")
    if not host:
        error("SSH host not configured. Set 'HA SSH Host' in plugin settings.")
        return
    port = str(CFG.get("homeassistant_ssh_port", "")).strip()
    cmd = ["ssh", "-t"]
    if port:
        cmd.extend(["-p", port])
    cmd.append(host)
    info(f"Connecting to {host}" + (f" port {port}" if port else "") + "...")
    log_action("SSH Shell", "Home Assistant")
    subprocess.run(cmd)


def _fetch_entities():
    """Fetch all entities and group by domain."""
    states = _api("/api/states")
    if not states or not isinstance(states, list):
        error("Could not fetch entities.")
        return None
    domains = {}
    for entity in states:
        entity_id = entity.get("entity_id", "")
        domain = entity_id.split(".")[0] if "." in entity_id else "other"
        domains.setdefault(domain, []).append(entity)
    return domains


def _pick_entity(domains, prompt_msg="Select domain:"):
    """Let user pick domain then entity. Returns entity dict or None."""
    sorted_domains = sorted(domains.keys())
    choices = [f"{d} ({len(domains[d])} entities)" for d in sorted_domains]
    choices.append("← Back")

    idx = pick_option(prompt_msg, choices)
    if idx >= len(sorted_domains):
        return None

    domain = sorted_domains[idx]
    entities = sorted(domains[domain], key=lambda x: x.get("entity_id", ""))

    entity_choices = []
    for e in entities:
        eid = e.get("entity_id", "?")
        state = e.get("state", "?")
        friendly = e.get("attributes", {}).get("friendly_name", "")
        label = f"{friendly or eid}  [{state}]"
        entity_choices.append(label)
    entity_choices.append("← Back")

    eidx = pick_option(f"{domain} entities:", entity_choices)
    if eidx >= len(entities):
        return None

    return entities[eidx]


def _browse_entities():
    domains = _fetch_entities()
    if not domains:
        return

    while True:
        entity = _pick_entity(domains)
        if not entity:
            return
        _show_entity_detail(entity)


def _resolve_service_call(entity_id, service):
    """Execute a favorited service call (fetches current name)."""
    domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
    # Try to get friendly name
    states = _api("/api/states")
    friendly = entity_id
    if states:
        for e in states:
            if e.get("entity_id") == entity_id:
                friendly = e.get("attributes", {}).get("friendly_name", entity_id)
                break
    _run_service_call(entity_id, domain, service, friendly)


def _show_entity_by_id(entity_id):
    """Fetch and show a single entity by ID (used by favorites)."""
    states = _api("/api/states")
    if not states:
        error("Could not fetch entities.")
        return
    for e in states:
        if e.get("entity_id") == entity_id:
            _show_entity_detail(e)
            return
    error(f"Entity {entity_id} not found.")


def _domain_actions(domain):
    """Return list of (label, service) tuples for an entity domain."""
    mapping = {
        "light": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "switch": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "fan": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "input_boolean": [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "button": [("Press", "press")],
        "input_button": [("Press", "press")],
        "automation": [("Trigger", "trigger"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "script": [("Run", "turn_on")],
        "scene": [("Activate", "turn_on")],
        "lock": [("Lock", "lock"), ("Unlock", "unlock")],
        "cover": [("Open", "open_cover"), ("Close", "close_cover"), ("Stop", "stop_cover")],
        "media_player": [("Play/Pause", "media_play_pause"), ("Stop", "media_stop"), ("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "climate": [("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "vacuum": [("Start", "start"), ("Stop", "stop"), ("Return home", "return_to_base")],
        "siren": [("Turn on", "turn_on"), ("Turn off", "turn_off")],
        "alarm_control_panel": [("Arm away", "alarm_arm_away"), ("Arm home", "alarm_arm_home"), ("Disarm", "alarm_disarm")],
        "number": [],
        "select": [],
        "sensor": [],
        "binary_sensor": [],
    }
    return mapping.get(domain, [("Toggle", "toggle"), ("Turn on", "turn_on"), ("Turn off", "turn_off")])


def _show_entity_detail(entity):
    eid = entity.get("entity_id", "?")
    attrs = entity.get("attributes", {})
    friendly = attrs.get("friendly_name", eid)
    domain = eid.split(".")[0]

    while True:
        # Re-fetch current state
        fresh = _api(f"/api/states/{eid}")
        if fresh:
            state = fresh.get("state", "?")
            attrs = fresh.get("attributes", {})
        else:
            state = entity.get("state", "?")

        print(f"\n  {C.BOLD}{friendly}{C.RESET}")
        print(f"  {C.DIM}Entity ID: {eid}{C.RESET}")
        print(f"  {C.BOLD}State:{C.RESET} {C.ACCENT}{state}{C.RESET}")

        if attrs:
            print(f"\n  {C.BOLD}Attributes:{C.RESET}")
            for k, v in attrs.items():
                if k != "friendly_name":
                    print(f"    {k}: {v}")

        domain_acts = _domain_actions(domain)
        action_choices = [label for label, _ in domain_acts]
        action_choices.extend(["★ Favorite", "← Back"])

        print()
        aidx = pick_option(f"Action for {friendly}:", action_choices)
        al = action_choices[aidx]

        if al == "← Back":
            return
        elif al == "★ Favorite":
            from homelab.plugins import add_item_favorite
            add_item_favorite("ha_entity", eid, friendly)
        else:
            # Find the matching service
            for label, svc in domain_acts:
                if label == al:
                    _api(f"/api/services/{domain}/{svc}", method="POST", data={"entity_id": eid})
                    success(f"{al}: {friendly}")
                    log_action("HA Service Call", f"{domain}.{svc} on {friendly}")
                    break


def _call_service():
    domains = _fetch_entities()
    if not domains:
        return

    entity = _pick_entity(domains, prompt_msg="Call service — select domain:")
    if not entity:
        return

    entity_id = entity.get("entity_id", "")
    friendly = entity.get("attributes", {}).get("friendly_name", entity_id)
    domain = entity_id.split(".")[0]

    domain_acts = _domain_actions(domain)
    service_choices = [f"{label} ({svc})" for label, svc in domain_acts]
    service_choices.append("Custom service")
    service_choices.append("Cancel")

    idx = pick_option(f"Service for {friendly}:", service_choices)
    if service_choices[idx] == "Cancel":
        return

    if service_choices[idx] == "Custom service":
        service = prompt_text("Service name (e.g. turn_on):")
        if not service:
            return
        label = service
    else:
        label, service = domain_acts[idx]

    while True:
        action_choices = ["Run now", "Run again", "★ Favorite", "← Back"]
        aidx = pick_option(f"{label} — {friendly}:", action_choices)
        al = action_choices[aidx]

        if al == "← Back":
            return
        elif al == "★ Favorite":
            from homelab.plugins import add_item_favorite
            add_item_favorite(
                "ha_service_call", entity_id,
                f"HA: {label} {friendly}",
                service=service,
            )
        elif al in ("Run now", "Run again"):
            _run_service_call(entity_id, domain, service, friendly)


def _run_service_call(entity_id, domain, service, friendly):
    """Execute a service call on an entity."""
    result = _api(
        f"/api/services/{domain}/{service}",
        method="POST",
        data={"entity_id": entity_id},
    )
    if result is not None:
        success(f"Called {domain}.{service} on {friendly}")
        log_action("HA Service Call", f"{domain}.{service} on {friendly}")
    else:
        error("Service call failed.")


# ─── Dashboard View ────────────────────────────────────────────────────────

def _dashboard_view():
    """Show a configurable at-a-glance view of key entities."""
    dashboard_ids = CFG.get("ha_dashboard_entities", [])

    while True:
        if not dashboard_ids:
            print(f"\n  {C.DIM}No dashboard entities configured.{C.RESET}")
            idx = pick_option("Dashboard:", ["+ Add entities", "← Back"])
            if idx == 1:
                return
            _configure_dashboard()
            dashboard_ids = CFG.get("ha_dashboard_entities", [])
            continue

        # Fetch all states once
        states = _api("/api/states")
        if not states:
            error("Could not fetch entity states.")
            return

        state_map = {e.get("entity_id"): e for e in states}

        hdr = f"\n  {C.BOLD}Home Assistant Dashboard{C.RESET}\n"
        choices = []
        entities = []
        for eid in dashboard_ids:
            entity = state_map.get(eid)
            if not entity:
                choices.append(f"{C.DIM}○{C.RESET} {eid}  {C.RED}not found{C.RESET}")
                entities.append(None)
                continue

            state = entity.get("state", "?")
            attrs = entity.get("attributes", {})
            friendly = attrs.get("friendly_name", eid)
            domain = eid.split(".")[0]
            unit = attrs.get("unit_of_measurement", "")

            # Color-code based on state
            if state in ("on", "home", "open", "unlocked"):
                icon = f"{C.GREEN}●{C.RESET}"
                state_str = f"{C.GREEN}{state}{C.RESET}"
            elif state in ("off", "away", "closed", "locked"):
                icon = f"{C.DIM}○{C.RESET}"
                state_str = f"{C.DIM}{state}{C.RESET}"
            elif state == "unavailable":
                icon = f"{C.RED}●{C.RESET}"
                state_str = f"{C.RED}{state}{C.RESET}"
            else:
                icon = f"{C.ACCENT}●{C.RESET}"
                state_str = f"{C.ACCENT}{state}{C.RESET}"

            if unit:
                state_str += f" {unit}"

            # Extra info for specific domains
            extra = ""
            if domain == "climate":
                temp = attrs.get("current_temperature", "")
                target = attrs.get("temperature", "")
                if temp:
                    extra = f"  (current: {temp}, target: {target})"
            elif domain == "media_player" and state == "playing":
                title = attrs.get("media_title", "")
                if title:
                    extra = f"  ({title})"

            choices.append(f"{icon} {friendly:<35} {state_str}{extra}")
            entities.append(entity)

        choices.append("───────────────")
        choices.append("Refresh")
        choices.append("Edit entity list")
        choices.append("← Back")

        idx = pick_option("Dashboard:", choices, header=hdr)

        if idx == len(choices) - 1:
            return
        elif choices[idx] == "Refresh":
            continue
        elif choices[idx] == "Edit entity list":
            _configure_dashboard()
            dashboard_ids = CFG.get("ha_dashboard_entities", [])
        elif choices[idx].startswith("───"):
            continue
        elif idx < len(entities) and entities[idx] is not None:
            _dashboard_entity_actions(entities[idx])


def _dashboard_entity_actions(entity):
    """Quick actions for a dashboard entity — toggle, turn on/off, etc."""
    eid = entity.get("entity_id", "?")
    attrs = entity.get("attributes", {})
    friendly = attrs.get("friendly_name", eid)
    domain = eid.split(".")[0]
    state = entity.get("state", "?")

    domain_acts = _domain_actions(domain)
    if not domain_acts:
        # Read-only entity (sensor, binary_sensor, number, select)
        _show_entity_detail(entity)
        return

    action_choices = [label for label, _ in domain_acts]
    action_choices.extend(["Details", "← Back"])

    hdr = f"\n  {C.BOLD}{friendly}{C.RESET}  {C.DIM}({eid}){C.RESET}\n  State: {C.ACCENT}{state}{C.RESET}\n"
    aidx = pick_option("", action_choices, header=hdr)
    action = action_choices[aidx]

    if action == "← Back":
        return
    elif action == "Details":
        _show_entity_detail(entity)
    else:
        for label, svc in domain_acts:
            if label == action:
                _api(f"/api/services/{domain}/{svc}", method="POST", data={"entity_id": eid})
                success(f"{action}: {friendly}")
                log_action("HA Service Call", f"{domain}.{svc} on {friendly}")
                break


def _configure_dashboard():
    """Let user pick entities for the dashboard view."""
    while True:
        current = CFG.get("ha_dashboard_entities", [])
        hdr_lines = [f"\n  {C.BOLD}Dashboard Entities{C.RESET} ({len(current)} configured)\n"]
        for eid in current:
            hdr_lines.append(f"    {eid}")
        hdr_lines.append("")

        idx = pick_option("Configure:", [
            "+ Add entities", "Remove entity", "Clear all", "← Done",
        ], header="\n".join(hdr_lines))

        if idx == 3:
            return
        elif idx == 2:
            CFG["ha_dashboard_entities"] = []
            save_config(CFG)
            success("Dashboard cleared.")
        elif idx == 1:
            if not current:
                warn("No entities to remove.")
                continue
            choices = current + ["Cancel"]
            ridx = pick_option("Remove which?", choices)
            if ridx < len(current):
                removed = current.pop(ridx)
                CFG["ha_dashboard_entities"] = current
                save_config(CFG)
                success(f"Removed: {removed}")
        elif idx == 0:
            # Let user pick from available entities
            domains = _fetch_entities()
            if not domains:
                continue
            entity = _pick_entity(domains, prompt_msg="Add entity to dashboard:")
            if entity:
                eid = entity.get("entity_id", "")
                if eid and eid not in current:
                    current.append(eid)
                    CFG["ha_dashboard_entities"] = current
                    save_config(CFG)
                    friendly = entity.get("attributes", {}).get("friendly_name", eid)
                    success(f"Added: {friendly}")
                elif eid in current:
                    warn(f"{eid} is already on the dashboard.")


# ─── Automations & Scenes ─────────────────────────────────────────────────

def _automations_and_scenes():
    """List automations and scenes with toggle/trigger actions."""
    while True:
        states = _api("/api/states")
        if not states:
            error("Could not fetch entities.")
            return

        automations = [e for e in states if e.get("entity_id", "").startswith("automation.")]
        scenes = [e for e in states if e.get("entity_id", "").startswith("scene.")]

        automations.sort(key=lambda e: e.get("attributes", {}).get("friendly_name", "").lower())
        scenes.sort(key=lambda e: e.get("attributes", {}).get("friendly_name", "").lower())

        choices = []
        items = []

        for a in automations:
            eid = a.get("entity_id", "?")
            friendly = a.get("attributes", {}).get("friendly_name", eid)
            state = a.get("state", "?")
            if state == "on":
                icon = f"{C.GREEN}●{C.RESET}"
            elif state == "off":
                icon = f"{C.DIM}○{C.RESET}"
            else:
                icon = f"{C.YELLOW}●{C.RESET}"
            last_triggered = a.get("attributes", {}).get("last_triggered", "")
            lt_str = f"  {C.DIM}last: {last_triggered[:16]}{C.RESET}" if last_triggered else ""
            choices.append(f"{icon} {friendly:<35} [{state}]{lt_str}")
            items.append(("automation", a))

        if scenes:
            choices.append(f"─── Scenes ({len(scenes)}) ───")
            items.append(("separator", None))
            for s in scenes:
                eid = s.get("entity_id", "?")
                friendly = s.get("attributes", {}).get("friendly_name", eid)
                choices.append(f"  {C.ACCENT}▸{C.RESET} {friendly}")
                items.append(("scene", s))

        choices.append("← Back")
        items.append(("back", None))

        hdr = f"\n  {C.BOLD}Automations & Scenes{C.RESET}  ({len(automations)} automations, {len(scenes)} scenes)\n"
        idx = pick_option("", choices, header=hdr)

        item_type, entity = items[idx]
        if item_type == "back":
            return
        elif item_type == "separator":
            continue
        elif item_type == "automation":
            _automation_actions(entity)
        elif item_type == "scene":
            _scene_activate(entity)


def _automation_actions(entity):
    """Actions for a single automation."""
    eid = entity.get("entity_id", "?")
    friendly = entity.get("attributes", {}).get("friendly_name", eid)
    state = entity.get("state", "?")
    last_triggered = entity.get("attributes", {}).get("last_triggered", "")

    print(f"\n  {C.BOLD}{friendly}{C.RESET}")
    print(f"  {C.DIM}ID: {eid}{C.RESET}")
    print(f"  State: {C.ACCENT}{state}{C.RESET}")
    if last_triggered:
        print(f"  Last triggered: {last_triggered[:19]}")

    toggle_label = "Disable" if state == "on" else "Enable"
    aidx = pick_option(f"{friendly}:", [
        "Trigger              — run this automation now",
        f"{toggle_label:<20} — turn {'off' if state == 'on' else 'on'}",
        "← Back",
    ])
    if aidx == 2:
        return
    elif aidx == 0:
        result = _api(
            "/api/services/automation/trigger", method="POST",
            data={"entity_id": eid})
        if result is not None:
            log_action("HA Trigger Automation", friendly)
            success(f"Triggered: {friendly}")
        else:
            error("Trigger failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
    elif aidx == 1:
        service = "turn_off" if state == "on" else "turn_on"
        result = _api(
            f"/api/services/automation/{service}", method="POST",
            data={"entity_id": eid})
        if result is not None:
            log_action(f"HA Automation {toggle_label}", friendly)
            success(f"{toggle_label}d: {friendly}")
        else:
            error(f"Failed to {toggle_label.lower()}.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


def _scene_activate(entity):
    """Activate a scene."""
    eid = entity.get("entity_id", "?")
    friendly = entity.get("attributes", {}).get("friendly_name", eid)

    if confirm(f"Activate scene: {friendly}?"):
        result = _api(
            "/api/services/scene/turn_on", method="POST",
            data={"entity_id": eid})
        if result is not None:
            log_action("HA Activate Scene", friendly)
            success(f"Activated: {friendly}")
        else:
            error("Activation failed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Log Viewer ───────────────────────────────────────────────────────────

def _log_viewer():
    """View recent state changes for an entity via the logbook API."""
    domains = _fetch_entities()
    if not domains:
        return

    entity = _pick_entity(domains, prompt_msg="View log for which entity:")
    if not entity:
        return

    eid = entity.get("entity_id", "")
    friendly = entity.get("attributes", {}).get("friendly_name", eid)

    end = time.strftime("%Y-%m-%dT%H:%M:%S")
    start_ts = time.time() - 86400
    start = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start_ts))

    entries = _api(f"/api/logbook/{start}?entity={eid}&end_time={end}")
    if not entries or not isinstance(entries, list):
        info(f"No log entries for {friendly} in the last 24 hours.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    rows = []
    for entry in entries:
        when = entry.get("when", "?")[:19]
        state_val = entry.get("state", "")
        message = entry.get("message", "")
        name = entry.get("name", friendly)
        display = f"{when}  {name}: {state_val}"
        if message:
            display += f"  {message}"
        rows.append(display)

    scrollable_list(f"Log: {friendly} (last 24h) — {len(rows)} entries", rows)


# ─── System Info ──────────────────────────────────────────────────────────

def _system_info():
    """Show Home Assistant system configuration and version."""
    ha_config = _api("/api/config")
    if not ha_config:
        error("Could not fetch HA config.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    print(f"\n  {C.BOLD}Home Assistant System Info{C.RESET}\n")
    print(f"  {C.BOLD}Version:{C.RESET}       {ha_config.get('version', '?')}")
    print(f"  {C.BOLD}Location:{C.RESET}      {ha_config.get('location_name', '?')}")
    unit_sys = ha_config.get("unit_system", {})
    if isinstance(unit_sys, dict):
        print(f"  {C.BOLD}Unit System:{C.RESET}   {unit_sys.get('length', '?')}")
    print(f"  {C.BOLD}Time Zone:{C.RESET}     {ha_config.get('time_zone', '?')}")
    print(f"  {C.BOLD}Elevation:{C.RESET}     {ha_config.get('elevation', '?')}m")
    print(f"  {C.BOLD}Config Dir:{C.RESET}    {ha_config.get('config_dir', '?')}")
    print(f"  {C.BOLD}State:{C.RESET}         {ha_config.get('state', '?')}")

    components = ha_config.get("components", [])
    if components:
        notable = [c for c in sorted(components) if not c.startswith("homeassistant")][:20]
        print(f"\n  {C.BOLD}Integrations:{C.RESET} {len(components)} loaded")
        for c in notable:
            print(f"    {c}")
        if len(components) > 20:
            print(f"    {C.DIM}... and {len(components) - 20} more{C.RESET}")

    print()
    input(f"  {C.DIM}Press Enter to continue...{C.RESET}")


# ─── Add-on Management ───────────────────────────────────────────────────

def _addon_management():
    """List and manage Home Assistant OS add-ons."""
    addons = _api("/api/hassio/addons", quiet=True)
    if not addons:
        warn("Add-on management unavailable (requires HA OS/Supervised).")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    addon_data = addons.get("data", {})
    if not isinstance(addon_data, dict):
        addon_data = {}
    addon_list = addon_data.get("addons", [])
    if not addon_list:
        info("No add-ons installed.")
        input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
        return

    while True:
        choices = []
        installed = []
        for addon in sorted(addon_list, key=lambda a: a.get("name", "").lower()):
            aname = addon.get("name", "?")
            state = addon.get("state", "?")
            version = addon.get("version", "?")
            update_available = addon.get("update_available", False)

            if state == "started":
                icon = f"{C.GREEN}●{C.RESET}"
            elif state == "stopped":
                icon = f"{C.DIM}○{C.RESET}"
            else:
                icon = f"{C.YELLOW}●{C.RESET}"

            update_str = f"  {C.YELLOW}update!{C.RESET}" if update_available else ""
            choices.append(f"{icon} {aname:<25} v{version}  [{state}]{update_str}")
            installed.append(addon)

        choices.append("← Back")
        idx = pick_option(f"Add-ons ({len(installed)}):", choices)
        if idx >= len(installed):
            return

        _addon_actions(installed[idx])


def _addon_actions(addon):
    """Show actions for a specific add-on."""
    slug = addon.get("slug", "?")
    aname = addon.get("name", "?")
    state = addon.get("state", "?")
    version = addon.get("version", "?")
    update_available = addon.get("update_available", False)

    print(f"\n  {C.BOLD}{aname}{C.RESET}")
    print(f"  Version: {version}")
    print(f"  State: {state}")
    if addon.get("description"):
        print(f"  {C.DIM}{addon['description'][:80]}{C.RESET}")

    action_items = []
    if state == "started":
        action_items.extend(["Stop", "Restart"])
    else:
        action_items.append("Start")
    if update_available:
        action_items.append("Update add-on")
    action_items.append("← Back")

    aidx = pick_option(f"{aname}:", action_items)
    action = action_items[aidx]

    if action == "← Back":
        return
    elif action == "Start":
        result = _api(f"/api/hassio/addons/{slug}/start", method="POST")
        if result is not None:
            log_action("HA Start Add-on", aname)
            success(f"Started: {aname}")
        else:
            error("Start failed.")
    elif action == "Stop":
        result = _api(f"/api/hassio/addons/{slug}/stop", method="POST")
        if result is not None:
            log_action("HA Stop Add-on", aname)
            success(f"Stopped: {aname}")
        else:
            error("Stop failed.")
    elif action == "Restart":
        result = _api(f"/api/hassio/addons/{slug}/restart", method="POST")
        if result is not None:
            log_action("HA Restart Add-on", aname)
            success(f"Restarted: {aname}")
        else:
            error("Restart failed.")
    elif action == "Update add-on":
        if confirm(f"Update {aname}?"):
            result = _api(f"/api/hassio/addons/{slug}/update", method="POST")
            if result is not None:
                log_action("HA Update Add-on", aname)
                success(f"Updated: {aname}")
            else:
                error("Update failed.")
    input(f"\n  {C.DIM}Press Enter to continue...{C.RESET}")
