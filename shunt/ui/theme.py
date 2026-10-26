"""Dark + green 'terminal' theme constants."""

import customtkinter as ctk

# palette
BG = "#060a06"            # window background — near-black with green tint
PANEL = "#0b120b"         # frame/panel background
BORDER = "#1d3a1d"        # subtle borders
GREEN = "#3dff73"         # primary accent (matrix-ish but readable)
GREEN_HOVER = "#2bd45f"
GREEN_DARK = "#123a1c"    # button backgrounds
GREEN_DEEP = "#0d2413"    # selected/hover recess
TEXT = "#c9ffc9"          # primary text
TEXT_DIM = "#5f9c6e"      # secondary labels
RED = "#ff5d5d"
AMBER = "#ffc14d"

FONT = ("Consolas", 12)
FONT_BOLD = ("Consolas", 12, "bold")
FONT_TITLE = ("Consolas", 15, "bold")
FONT_MONO = ("Consolas", 11)


def apply():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")


def _style_widget_defaults():
    """Shared kwargs factories so widgets stay consistent."""
    return {
        "entry": dict(fg_color=PANEL, border_color=BORDER, text_color=GREEN,
                      font=FONT, corner_radius=4),
        "button": dict(fg_color=GREEN_DARK, hover_color=GREEN_HOVER,
                       text_color="#eaffea", font=FONT_BOLD, corner_radius=4,
                       border_width=1, border_color=BORDER),
        "label": dict(text_color=TEXT, font=FONT),
        "dim": dict(text_color=TEXT_DIM, font=FONT),
        "frame": dict(fg_color=PANEL, border_color=BORDER, border_width=1,
                      corner_radius=6),
    }


W = _style_widget_defaults()
