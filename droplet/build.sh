#!/usr/bin/env bash
# Build "Blend ke Blender 3.app" (droplet drag-and-drop macOS) dari sumbernya.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
APP="$ROOT/Blend ke Blender 3.app"
rm -rf "$APP"
osacompile -o "$APP" "$HERE/droplet.applescript"
cp "$ROOT/blend2v3.sh" "$ROOT/legacy_export.py" "$ROOT/legacy_import.py" "$APP/Contents/Resources/"
echo "Selesai -> $APP"
