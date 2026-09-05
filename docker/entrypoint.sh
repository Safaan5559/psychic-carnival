#!/usr/bin/env bash
set -euo pipefail
cd /workspace/app

APP_NAME="${PY2APK_APP_NAME:-Python App}"
PACKAGE="${PY2APK_PACKAGE:-com.py2apk.app}"
VERSION="${PY2APK_VERSION:-1.0.0}"
REQS="${PY2APK_REQUIREMENTS:-python3,kivy}"
ICON="${PY2APK_ICON:-}"
SPLASH="${PY2APK_SPLASH:-}"

python3 - <<'PY'
import os,re
for key in ('PY2APK_PACKAGE','PY2APK_VERSION','PY2APK_REQUIREMENTS'):
    value=os.environ.get(key,'')
    if not re.fullmatch(r'[A-Za-z0-9_.,+\- ]+',value):
        raise SystemExit(f'Unsafe value in {key}')
PY

cat > buildozer.spec <<EOF
[app]
title = ${APP_NAME}
package.name = $(echo "$PACKAGE" | awk -F. '{print $NF}')
package.domain = $(echo "$PACKAGE" | awk -F. 'BEGIN{OFS="."}{$NF=""; sub(/\.$/,""); print}')
source.dir = .
source.include_exts = py,png,jpg,jpeg,webp,kv,atlas,ttf,txt,json
requirements = ${REQS}
orientation = portrait
fullscreen = 0
android.api = 36
android.minapi = 21
android.ndk = 28.2.13676358
android.accept_sdk_license = True
android.archs = arm64-v8a,armeabi-v7a
version = ${VERSION}
EOF

if [ -n "$ICON" ] && [ -f "$ICON" ]; then echo "icon.filename = $ICON" >> buildozer.spec; fi
if [ -n "$SPLASH" ] && [ -f "$SPLASH" ]; then echo "presplash.filename = $SPLASH" >> buildozer.spec; fi

# Build debug APK. Release signing belongs in a separate controlled deployment pipeline.
buildozer -v android debug
mkdir -p /workspace/out
find bin -type f -name '*.apk' -print -exec cp {} /workspace/out/ \;
