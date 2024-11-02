#!/usr/bin/env python3
from hashlib import md5
import json
from os import path
from os import geteuid
import socket
import sys
from string import ascii_letters

# taken from https://github.com/naggie/dotfiles/blob/master/app-configurators/scripts/bin/system-colour

# WCAG contrast calculation functions based on
# https://github.com/gsnedders/wcag-contrast-ratio/blob/master/wcag_contrast_ratio/contrast.py
# Copyright (c) 2015 Geoffrey Sneddon.
# license: app-configurators/scripts/etc/wcag-contrast-ratio-LICENSE.md

# JSON data file provided by jonasjacek
# https://github.com/jonasjacek/colors/blob/master/data.json
# license: app-configurators/scripts/etc/colors-LICENSE.md

# TODO use machine id for more entropy? Could help with unreachable colours when matching hostname colour

with open(path.expanduser("~/.share/256-terminal-colour-map.json")) as f:
    COLOUR_LIST = json.load(f)


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
    print("SYSTEM_COLOUR_FG=9")
    print("SYSTEM_COLOUR_BG=0")
    sys.exit()

hostname = socket.gethostname()

# use simple hostname (not FQDN) for stable value -- search domain could
# otherwise change the colour
hostname = hostname.split(".")[0]

# also, macos has a strange bug that says another host has the same name,
# resulting in appending a number in brackets. Remove the brackets, if there
# are any
hostname = hostname.split("(")[0]
hostname = hostname.split("-")[0]

tiebreaker = ""
if path.exists("/etc/machine-id"):
    with open("/etc/machine-id") as f:
        tiebreaker = f.read()

fg, bg = get_colours(hostname, tiebreaker)

# NOTE: run against /usr/share/dict/words for a good idea of variance
# print(colourise("".join(argv[1:])))

print("SYSTEM_COLOUR_FG=%s" % fg["colorId"])
print("SYSTEM_COLOUR_BG=%s" % bg["colorId"])
