#!/bin/sh
# Builds Kyosei.app and a zip of it under .build/package. With KYOSEI_SIGN_IDENTITY it is
# signed for distribution, and with the three KYOSEI_NOTARY_* variables also notarized.
set -eu
cd "$(dirname "$0")"

OUT=.build/package
APP="$OUT/Kyosei.app"

(cd web && npm ci --no-fund --no-audit && npm run build)
swift build -c release --arch arm64 --arch x86_64
PRODUCTS=$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)

rm -rf "$OUT"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PRODUCTS/KyoseiMac" "$APP/Contents/MacOS/Kyosei"
cp -R "$PRODUCTS/KyoseiApple_KyoseiMac.bundle" "$APP/Contents/Resources/"
cp Packaging/Info.plist "$APP/Contents/Info.plist"

if [ -n "${KYOSEI_SIGN_IDENTITY:-}" ]; then
  codesign --force --options runtime --timestamp --sign "$KYOSEI_SIGN_IDENTITY" "$APP"
else
  codesign --force --sign - "$APP"
fi

ditto -c -k --keepParent "$APP" "$OUT/Kyosei.zip"

if [ -n "${KYOSEI_NOTARY_KEY:-}" ]; then
  xcrun notarytool submit "$OUT/Kyosei.zip" --key "$KYOSEI_NOTARY_KEY" \
    --key-id "$KYOSEI_NOTARY_KEY_ID" --issuer "$KYOSEI_NOTARY_ISSUER" --wait
  xcrun stapler staple "$APP"
  ditto -c -k --keepParent "$APP" "$OUT/Kyosei.zip"
fi
