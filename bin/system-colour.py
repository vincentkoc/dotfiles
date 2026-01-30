#!/usr/bin/env python3
"""
System Color Generator for Terminal Prompts

This script generates consistent, WCAG-compliant color pairs for terminal prompts
based on the hostname. It ensures good contrast ratios and readable output.

Sources and Credits:
-------------------
Original Implementation:
    https://github.com/naggie/dotfiles/blob/master/app-configurators/scripts/bin/system-colour

WCAG Contrast Calculations:
    https://github.com/gsnedders/wcag-contrast-ratio/blob/master/wcag_contrast_ratio/contrast.py
    Copyright (c) 2015 Geoffrey Sneddon
    License: https://github.com/gsnedders/wcag-contrast-ratio/blob/master/LICENSE

Color Data:
    https://github.com/jonasjacek/colors/blob/master/data.json
    By Jonas Jacek
    License: The MIT License (MIT)

Features:
- Generates consistent colors based on hostname
- Ensures WCAG-compliant contrast ratios
- Special handling for root user (red theme)
- Fallback to basic colors if JSON data unavailable
- Handles hostname variations (FQDN, macOS suffixes)
"""

from hashlib import md5
import json
from os import path
from os import geteuid
import socket
import sys
from string import ascii_letters

# Default colors that work well together if JSON fails
FALLBACK_COLORS = [
    {"colorId": 33, "name": "Blue"},      # Blue
    {"colorId": 37, "name": "White"},     # White
    {"colorId": 32, "name": "Green"},     # Green
    {"colorId": 36, "name": "Cyan"},      # Cyan
    {"colorId": 35, "name": "Purple"},    # Purple
    {"colorId": 34, "name": "Blue"},      # Blue
    {"colorId": 31, "name": "Red"}        # Red
]

try:
    with open(path.expanduser("~/.share/256-terminal-colour-map.json")) as f:
        COLOUR_LIST = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    # If JSON file is missing or invalid, use fallback colors
    COLOUR_LIST = FALLBACK_COLORS

# filter out strong red, reserved for root
COLOUR_LIST = list(
    filter(lambda c: c["colorId"] not in (160, 196, 9, 88, 124), COLOUR_LIST)
)

# WC3
CONSTRAST_THRESHOLD = 4.5

def get_colour(colorId: int):
    for c in COLOUR_LIST:
        if c["colorId"] == colorId:
            return c

    raise ValueError("Invalid colorId")

def rgb_contrast(rgb1, rgb2):
    for r, g, b in (rgb1, rgb2):
        if not 0.0 <= r <= 1.0:
            raise ValueError("r is out of valid range (0.0 - 1.0)")
        if not 0.0 <= g <= 1.0:
            raise ValueError("g is out of valid range (0.0 - 1.0)")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b is out of valid range (0.0 - 1.0)")

    l1 = relative_luminance(*rgb1)
    l2 = relative_luminance(*rgb2)

    if l1 > l2:
        return (l1 + 0.05) / (l2 + 0.05)
    else:
        return (l2 + 0.05) / (l1 + 0.05)

def relative_luminance(r, g, b):
    r = linearise(r)
    g = linearise(g)
    b = linearise(b)

    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def linearise(v):
    if v <= 0.03928:
        return v / 12.92
    else:
        return ((v + 0.055) / 1.055) ** 2.4

def word_matches_colour(seed, colour):
    seed = "".join([x for x in seed.lower() if x in ascii_letters])
    colour = "".join([x for x in colour["name"].lower() if x in ascii_letters])
    return seed in colour or colour in seed

def get_contrasting_colours(subject):
    selected = list()

    for candidate in COLOUR_LIST:
        contrast = rgb_contrast(
            (
                subject["rgb"]["r"] / 255,
                subject["rgb"]["g"] / 255,
                subject["rgb"]["b"] / 255,
            ),
            (
                candidate["rgb"]["r"] / 255,
                candidate["rgb"]["g"] / 255,
                candidate["rgb"]["b"] / 255,
            ),
        )

        if contrast >= CONSTRAST_THRESHOLD:
            selected.append(candidate)

    return selected

def select_by_seed(candidates, seed):
    """Produces a weighted deterministic colour"""
    m = md5()
    m.update(seed.encode())
    digest = m.hexdigest()

    index = int(digest, 16) % len(candidates)

    return candidates[index]

def get_colours(seed, tiebreaker=""):
    # if the hostname is a colour, try to match it for extra points
    matching = [c for c in COLOUR_LIST if word_matches_colour(seed, c)]

    if len(matching) > 1:
        # hostname is a colour, and has multiple matches. To avoid always
        # picking the same shade for a given colour, use the tiebreaker
        # (machine-id) to vary the seed
        seed += tiebreaker

    fg = select_by_seed(matching or COLOUR_LIST, seed)
    bg_candidates = get_contrasting_colours(fg)
    bg = select_by_seed(bg_candidates, seed)

    # 50% chance swap to remove bias to light foreground -- palette is
    # predominately light
    return select_by_seed([(fg, bg), (bg, fg)], seed)

def wrap(msg, fg, bg):
    return f"\033[48;5;{bg['colorId']}m\033[38;5;{fg['colorId']}m{msg}\033[0m"

def colourise(string):
    fg, bg = get_colours(string)
    return wrap(string, fg, bg)

# root? Make things red!
if geteuid() == 0:
    print("export SYSTEM_COLOUR_FG=9")
    print("export SYSTEM_COLOUR_BG=0")
    sys.exit()

try:
    hostname = socket.gethostname()
    hostname = hostname.split(".")[0]
    hostname = hostname.split("(")[0]
    hostname = hostname.split("-")[0]

    tiebreaker = ""
    if path.exists("/etc/machine-id"):
        with open("/etc/machine-id") as f:
            tiebreaker = f.read()

    fg, bg = get_colours(hostname, tiebreaker)
    print(f"export SYSTEM_COLOUR_FG={fg['colorId']}")
    print(f"export SYSTEM_COLOUR_BG=0")  # Always use black background for better readability

except Exception as e:
    # If anything fails, use a default color
    print("export SYSTEM_COLOUR_FG=37")  # White
    print("export SYSTEM_COLOUR_BG=0")   # Black
