#!/usr/bin/env bash

set -euo pipefail

PRESET_NAME="Tokyo Night"
PREFS_FILE="$HOME/Library/Preferences/com.googlecode.iterm2.plist"

if pgrep -x "iTerm2" >/dev/null 2>&1; then
  echo "Please quit iTerm2 before applying the $PRESET_NAME color preset." >&2
  exit 1
fi

if [ ! -f "$PREFS_FILE" ]; then
  echo "iTerm2 preferences not found at $PREFS_FILE" >&2
  exit 1
fi

python3 <<'PY'
import os
import plistlib
import sys

preset_name = "Tokyo Night"
prefs_path = os.path.expanduser("~/Library/Preferences/com.googlecode.iterm2.plist")

palette = {
    "Ansi 0 Color": "15161e",
    "Ansi 1 Color": "f7768e",
    "Ansi 2 Color": "9ece6a",
    "Ansi 3 Color": "e0af68",
    "Ansi 4 Color": "7aa2f7",
    "Ansi 5 Color": "bb9af7",
    "Ansi 6 Color": "7dcfff",
    "Ansi 7 Color": "a9b1d6",
    "Ansi 8 Color": "414868",
    "Ansi 9 Color": "ff7a93",
    "Ansi 10 Color": "b9f27c",
    "Ansi 11 Color": "ff9e64",
    "Ansi 12 Color": "7da6ff",
    "Ansi 13 Color": "bb9af7",
    "Ansi 14 Color": "7dcfff",
    "Ansi 15 Color": "c0caf5",
    "Background Color": "1a1b26",
    "Foreground Color": "c0caf5",
    "Bold Color": "c0caf5",
    "Cursor Color": "c0caf5",
    "Cursor Text Color": "1a1b26",
    "Cursor Guide Color": "3b4261",
    "Selection Color": "283457",
    "Selected Text Color": "c0caf5",
    "Link Color": "7aa2f7",
    "Badge Color": "ff9e64",
}

def hex_to_color(code: str) -> dict:
    r = int(code[0:2], 16) / 255
    g = int(code[2:4], 16) / 255
    b = int(code[4:6], 16) / 255
    return {
        "Color Space": "sRGB",
        "Alpha Component": 1.0,
        "Red Component": r,
        "Green Component": g,
        "Blue Component": b,
    }

try:
    with open(prefs_path, "rb") as handle:
        data = plistlib.load(handle)
except FileNotFoundError:
    print(f"Preferences file not found at {prefs_path}", file=sys.stderr)
    sys.exit(1)

custom_presets = data.setdefault("Custom Color Presets", {})
custom_presets[preset_name] = {key: hex_to_color(value) for key, value in palette.items()}

with open(prefs_path, "wb") as handle:
    plistlib.dump(data, handle)
PY

echo "Imported '$PRESET_NAME' color preset."
echo "Open iTerm2 > Settings > Profiles > Colors and select '$PRESET_NAME'."
