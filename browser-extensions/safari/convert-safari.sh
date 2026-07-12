#!/usr/bin/env bash
# convert-safari.sh — wrap TailCam Companion in a Safari (Xcode) project.
#
# Safari can't load a plain WebExtension directory; it needs the extension
# embedded in a native app. Apple ships a converter with Xcode that generates
# that wrapper project. This script:
#
#   1. assembles the safari package with the repo's stdlib-only build script
#      (python3 ../build.py --targets safari --no-zip --out ./.build), and
#   2. runs `xcrun safari-web-extension-converter` on the assembled directory,
#      producing an Xcode project in ./TailCamCompanion.
#
# By default the converter generates a *universal* project (macOS + iOS
# targets). We pass --macos-only below because TailCam Companion is built and
# tested for desktop Safari; drop that flag if you want the iOS/iPadOS target
# scaffolding too.
#
# Local use (no Apple Developer account needed):
#   - open the generated project in Xcode and press Run once, then
#   - Safari -> Settings -> Extensions -> enable "TailCam Companion".
#   - During development also enable Develop -> Allow Unsigned Extensions
#     (turn on the Develop menu in Safari Settings -> Advanced; the unsigned
#     toggle resets every time Safari quits).
#
# Distributing to anyone else — including via the App Store — requires a paid
# Apple Developer account for signing/notarization.

set -euo pipefail

# Everything is relative to this script's directory (browser-extensions/safari).
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "error: this script must run on macOS — safari-web-extension-converter ships with Xcode." >&2
    exit 1
fi

if ! command -v xcrun >/dev/null 2>&1; then
    echo "error: xcrun not found. Install Xcode (or the Command Line Tools) from the App Store," >&2
    echo "       then run: sudo xcode-select --switch /Applications/Xcode.app" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found (needed by build.py)." >&2
    exit 1
fi

echo "==> Assembling safari package (shared/ + safari/manifest.json)"
python3 ../build.py --targets safari --no-zip --out ./.build

echo "==> Generating Xcode project in ./TailCamCompanion"
xcrun safari-web-extension-converter ./.build/safari \
    --project-location ./TailCamCompanion \
    --app-name "TailCam Companion" \
    --bundle-identifier io.github.factshin.tailcam.companion \
    --swift \
    --macos-only \
    --no-open

echo
echo "Done. Next steps:"
echo "  1. open \"TailCamCompanion/TailCam Companion/TailCam Companion.xcodeproj\" in Xcode and Run once"
echo "  2. Safari -> Settings -> Extensions -> enable TailCam Companion"
echo "  3. for unsigned dev builds: Develop -> Allow Unsigned Extensions"
