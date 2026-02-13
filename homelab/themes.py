"""Theme system — preset color palettes for the UI."""

from homelab.config import CFG, save_config
from homelab.ui import C, pick_option, prompt_text, success, error, rebuild_style

# Each theme defines an accent color that drives C.ACCENT and the questionary style.
THEMES = {
    "default": {
        "accent_color": "#bd93f9",
        "description": "Purple haze (Dracula)",
    },
    "dracula": {
        "accent_color": "#bd93f9",
        "description": "Purple haze",
    },
    "nord": {
        "accent_color": "#88c0d0",
        "description": "Arctic frost blue",
    },
    "catppuccin": {
        "accent_color": "#cba6f7",
        "description": "Soft lavender (Mocha)",
    },
    "gruvbox": {
        "accent_color": "#fabd2f",
        "description": "Warm retro yellow",
    },
    "tokyo night": {
        "accent_color": "#7aa2f7",
        "description": "Neon blue glow",
    },
    "solarized": {
        "accent_color": "#268bd2",
        "description": "Classic blue",
    },
    "rose pine": {
        "accent_color": "#c4a7e7",
        "description": "Muted iris purple",
    },
    "monokai": {
        "accent_color": "#a6e22e",
        "description": "Vivid green",
    },
    "ocean": {
        "accent_color": "#6699cc",
        "description": "Deep sea blue",
    },
}


def _preview_swatch(hex_color):
    """Return a small colored swatch string."""
    from homelab.ui import hex_to_ansi
    ansi = hex_to_ansi(hex_color)
    return f"{ansi}████{C.RESET}"


def pick_theme():
    """Let the user pick a theme from the preset list or enter a custom color."""
    current = CFG.get("theme", "default")
    choices = []
    theme_names = list(THEMES.keys())
    for name in theme_names:
        theme = THEMES[name]
        swatch = _preview_swatch(theme["accent_color"])
        marker = " (current)" if name == current else ""
        choices.append(f"{swatch}  {name.title():<16} {theme['description']}{marker}")

    # Custom option with current color swatch
    current_color = CFG.get("accent_color", "#5f9ea0")
    custom_swatch = _preview_swatch(current_color)
    custom_marker = " (current)" if current == "custom" else ""
    choices.append(f"{custom_swatch}  {'Custom':<16} Enter a hex color{custom_marker}")
    choices.append("← Back")

    idx = pick_option("Choose a theme:", choices)

    if idx >= len(theme_names) + 1:
        return  # Back

    if idx == len(theme_names):
        # Custom color
        val = prompt_text(f"Hex color (e.g. #ff6600) [{current_color}]:")
        if not val:
            return
        val = val.strip().lstrip("#")
        if len(val) == 6 and all(c in "0123456789abcdefABCDEF" for c in val):
            CFG["theme"] = "custom"
            CFG["accent_color"] = f"#{val}"
            save_config(CFG)
            rebuild_style()
            success(f"Custom color set to #{val}")
        else:
            error("Invalid hex color. Use format: #ff6600")
        return

    name = theme_names[idx]
    theme = THEMES[name]
    CFG["theme"] = name
    CFG["accent_color"] = theme["accent_color"]
    save_config(CFG)
    rebuild_style()
    success(f"Theme set to {name.title()} ({theme['accent_color']})")
