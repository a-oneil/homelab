"""Colors, prompt helpers, and UI utilities."""

import re
import shutil
import threading

import questionary
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from questionary import Style

from homelab.config import CFG


def hex_to_ansi(hex_color):
    """Convert a hex color like '#5f9ea0' to a truecolor ANSI escape."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "\033[36m"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"\033[38;2;{r};{g};{b}m"


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    ACCENT = hex_to_ansi(CFG.get("accent_color", "#5f9ea0"))


def _build_style():
    accent = CFG.get("accent_color", "#5f9ea0")
    return Style([
        ("qmark", f"fg:{accent} bold"),
        ("question", "fg:white bold"),
        ("pointer", f"fg:{accent} bold"),
        ("highlighted", f"fg:{accent} bold"),
        ("selected", "fg:green"),
        ("answer", "fg:green bold"),
        ("instruction", "fg:#888888"),
        ("separator", "fg:#888888"),
    ])


STYLE = _build_style()

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# Thread-local flag: when set, print helpers become no-ops.
# Used by background threads to prevent corrupting the interactive prompt.
_tlocal = threading.local()


def suppress_output(suppress=True):
    """Set/clear the output suppression flag for the current thread."""
    _tlocal.suppress = suppress


def _is_suppressed():
    return getattr(_tlocal, "suppress", False)


def strip_ansi(text):
    return ANSI_RE.sub("", text)


def info(msg):
    if not _is_suppressed():
        print(f"  {C.ACCENT}{msg}{C.RESET}")


def success(msg):
    if not _is_suppressed():
        print(f"  {C.GREEN}{msg}{C.RESET}")


def error(msg):
    if not _is_suppressed():
        print(f"  {C.RED}{msg}{C.RESET}")


def warn(msg):
    if not _is_suppressed():
        print(f"  {C.YELLOW}{msg}{C.RESET}")


def clear_screen():
    """Clear the terminal screen."""
    print("\033[2J\033[H", end="", flush=True)


def pick_option(prompt, options, header=""):
    """Arrow-key select with type-to-filter. Returns selected index."""
    clear_screen()
    if header:
        print(header)
    clean_prompt = strip_ansi(prompt).strip() if prompt else "Select:"
    clean_options = [strip_ansi(o) for o in options]
    if not clean_options:
        return 0

    question = questionary.select(
        clean_prompt,
        choices=clean_options,
        style=STYLE,
        use_shortcuts=False,
        use_indicator=True,
        use_search_filter=True,
        use_jk_keys=False,
        instruction="(↑↓ navigate, type to filter, Ctrl-G to go back)",
    )

    back_kb = KeyBindings()

    @back_kb.add(Keys.ControlG, eager=True)
    def _go_back(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    app = question.application
    app.key_bindings = merge_key_bindings([app.key_bindings, back_kb])

    try:
        result = question.unsafe_ask()
    except KeyboardInterrupt:
        return len(options) - 1

    if result is None:
        return len(options) - 1
    return clean_options.index(result)


def pick_multi(prompt, options, header=""):
    """Multi-select with checkboxes. Returns list of selected indices."""
    clear_screen()
    if header:
        print(header)
    clean_prompt = strip_ansi(prompt).strip() if prompt else "Select (Space to toggle):"
    clean_options = [strip_ansi(o) for o in options]
    if not clean_options:
        return []
    question = questionary.checkbox(
        clean_prompt, choices=clean_options, style=STYLE,
        use_jk_keys=False,
        instruction="(↑↓ navigate, Space toggle, Enter confirm, Ctrl-G cancel)",
    )
    back_kb = KeyBindings()

    @back_kb.add(Keys.ControlG, eager=True)
    def _go_back(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    app = question.application
    app.key_bindings = merge_key_bindings([app.key_bindings, back_kb])
    try:
        selected = question.unsafe_ask()
    except KeyboardInterrupt:
        return []
    if selected is None:
        return []
    return [clean_options.index(s) for s in selected]


def confirm(msg, default_yes=True):
    clean = strip_ansi(msg)
    result = questionary.confirm(clean, default=default_yes, style=STYLE).ask()
    if result is None:
        return False
    return result


def prompt_text(msg, default=""):
    clean = strip_ansi(msg)
    result = questionary.text(clean, default=default, style=STYLE).ask()
    if result is None:
        return ""
    return result.strip()


def check_tool(tool_name):
    return shutil.which(tool_name) is not None


def bar_chart(used, total, width=30):
    """Return a text bar chart like [████████░░░░░░░░░░] 45%."""
    if total <= 0:
        return "[" + "?" * width + "] ??%"
    pct = used / total
    filled = int(pct * width)
    empty = width - filled
    if pct > 0.9:
        color = C.RED
    elif pct > 0.7:
        color = C.YELLOW
    else:
        color = C.GREEN
    return f"{color}[{'█' * filled}{'░' * empty}]{C.RESET} {pct * 100:.0f}%"


def rebuild_style():
    """Rebuild questionary style after accent color change."""
    global STYLE
    C.ACCENT = hex_to_ansi(CFG.get("accent_color", "#5f9ea0"))
    STYLE = _build_style()
