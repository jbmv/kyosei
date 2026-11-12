#!/bin/sh
# Builds Kyosei.app and a zip of it under .build/package. With KYOSEI_SIGN_IDENTITY it is
# signed for distribution, and with the three KYOSEI_NOTARY_* variables also notarized.
# With KYOSEI_PROVISIONING_PROFILE (a Developer ID provisioning profile for the app's
# identifier) and KYOSEI_TEAM_ID beside the identity, the profile is embedded and the app is
# signed with the entitlements that let its executable keep the seal key in iCloud Keychain
# (KYO-789). Without them the package is as it was. Neither value is kept in the repository.
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

if [ -n "${KYOSEI_SIGN_IDENTITY:-}" ] && [ -n "${KYOSEI_PROVISIONING_PROFILE:-}" ]; then
  : "${KYOSEI_TEAM_ID:?KYOSEI_PROVISIONING_PROFILE needs KYOSEI_TEAM_ID, the team the profile was issued to}"
  BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' Packaging/Info.plist)
  cp "$KYOSEI_PROVISIONING_PROFILE" "$APP/Contents/embedded.provisionprofile"
  cat > "$OUT/Kyosei.entitlements" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.application-identifier</key>
  <string>$KYOSEI_TEAM_ID.$BUNDLE_ID</string>
  <key>com.apple.developer.team-identifier</key>
  <string>$KYOSEI_TEAM_ID</string>
  <key>keychain-access-groups</key>
  <array>
    <string>$KYOSEI_TEAM_ID.$BUNDLE_ID</string>
  </array>
</dict>
</plist>
EOF
  codesign --force --options runtime --timestamp --entitlements "$OUT/Kyosei.entitlements" \
    --sign "$KYOSEI_SIGN_IDENTITY" "$APP"
elif [ -n "${KYOSEI_SIGN_IDENTITY:-}" ]; then
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
