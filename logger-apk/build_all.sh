#!/usr/bin/env bash
# Build one logger APK per instance from the fleet manifest.
#   logger-apk/build_all.sh fleet/out/manifest.json out/apks
set -euo pipefail

MANIFEST="${1:?path to fleet manifest.json (make fleet)}"
OUT="${2:-out/apks}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"

python3 - "$MANIFEST" <<'PY' | while read -r id url; do
import json, sys
for i in json.load(open(sys.argv[1])):
    print(i["id"], i["ingest_url"])
PY
  echo ">> $id -> $url"
  (cd "$HERE" && ./gradlew -q assembleDebug -PinstanceId="$id" -PingestUrl="$url")
  cp "$HERE/app/build/outputs/apk/debug/notif-logger-$id-debug.apk" "$OUT/"
done
ls -1 "$OUT"
