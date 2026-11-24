#!/usr/bin/env bash
# Konversi .blend dari Blender baru ke Blender lama dalam satu langkah
# (kalau dua-duanya terpasang di komputer yang sama).
#
#   ./downgrade.sh BLENDER_BARU BLENDER_LAMA input.blend output.blend
#
# Contoh (macOS):
#   ./downgrade.sh /Applications/Blender.app/Contents/MacOS/Blender \
#                  /Applications/Blender3.6.app/Contents/MacOS/Blender \
#                  karakter.blend karakter_v3.blend
set -euo pipefail

if [ $# -lt 4 ]; then
  sed -n '2,10p' "$0"; exit 1
fi
NEW="$1"; OLD="$2"; IN="$3"; OUT="$4"; shift 4
HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$(mktemp -d)/pkg"

"$NEW" -b --factory-startup "$IN" --python "$HERE/legacy_export.py" -- "$PKG" "$@"
"$OLD" -b --factory-startup --python "$HERE/legacy_import.py" -- "$PKG" "$OUT"
rm -rf "$(dirname "$PKG")"
echo "Selesai -> $OUT"
