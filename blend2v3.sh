#!/usr/bin/env bash
# Ubah file .blend dari Blender 4.x/5.x menjadi file .blend Blender 3.x.
#
#   ./blend2v3.sh file.blend [file2.blend ...]
#
# Hasil: file_v3.blend di folder yang sama dengan file aslinya.
# Blender baru (4.x/5.x) dan lama (3.x) dicari otomatis di /Applications.
# Bisa juga ditentukan manual:  BLENDER_NEW=... BLENDER_OLD=... ./blend2v3.sh file.blend
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

major_of() { "$1" --version 2>/dev/null | awk '/^Blender [0-9]/{split($2,v,".");print v[1];exit}'; }

find_blenders() {
  local exe maj
  for app in /Applications/Blender*.app "$HOME"/Applications/Blender*.app; do
    exe="$app/Contents/MacOS/Blender"
    [ -x "$exe" ] || continue
    maj="$(major_of "$exe")"
    if [ -z "${BLENDER_NEW:-}" ] && [ "${maj:-0}" -ge 4 ]; then BLENDER_NEW="$exe"; fi
    if [ -z "${BLENDER_OLD:-}" ] && [ "${maj:-0}" -eq 3 ]; then BLENDER_OLD="$exe"; fi
  done
}

if [ $# -lt 1 ]; then sed -n '2,9p' "$0"; exit 1; fi

find_blenders
if [ -z "${BLENDER_NEW:-}" ]; then echo "GAGAL: Blender 4.x/5.x tidak ditemukan di /Applications."; exit 1; fi
if [ -z "${BLENDER_OLD:-}" ]; then echo "GAGAL: Blender 3.x tidak ditemukan di /Applications."; exit 1; fi
echo "Blender baru : $BLENDER_NEW"
echo "Blender lama : $BLENDER_OLD"

ok=0; fail=0
for IN in "$@"; do
  case "$IN" in *.blend) ;; *) echo "Lewati (bukan .blend): $IN"; continue;; esac
  IN="$(cd "$(dirname "$IN")" && pwd)/$(basename "$IN")"
  OUT="${IN%.blend}_v3.blend"
  TMP="$(mktemp -d)"
  echo; echo "=== $(basename "$IN") ==="
  if "$BLENDER_NEW" -b --factory-startup "$IN" --python "$HERE/legacy_export.py" -- "$TMP/pkg" \
       >"$TMP/export.log" 2>&1 \
     && "$BLENDER_OLD" -b --factory-startup --python "$HERE/legacy_import.py" -- "$TMP/pkg" "$OUT" \
       >"$TMP/import.log" 2>&1 \
     && [ -f "$OUT" ]; then
    grep -h "PERINGATAN" "$TMP/export.log" "$TMP/import.log" | sort -u | sed 's/^/  /'
    echo "OK -> $OUT"; ok=$((ok+1))
  else
    echo "GAGAL. Log:"; tail -n 20 "$TMP/export.log" "$TMP/import.log" 2>/dev/null
    fail=$((fail+1))
  fi
  rm -rf "$TMP"
done
echo; echo "Selesai: $ok berhasil, $fail gagal."
[ $fail -eq 0 ]
