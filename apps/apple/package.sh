#!/bin/sh
# Builds Kyosei.app and a zip of it under .build/package. With KYOSEI_SIGN_IDENTITY it is
# signed for distribution, and with the three KYOSEI_NOTARY_* variables also notarized.
# With KYOSEI_PROVISIONING_PROFILE (a Developer ID provisioning profile for the app's
# identifier) and KYOSEI_TEAM_ID beside the identity, the profile is embedded and the app is
# signed with the entitlements that let its executable keep the seal key in iCloud Keychain
# (KYO-789). Without them the package is as it was. Neither value is kept in the repository.
#
# `package.sh check` builds the check build instead (KYO-944): KyoseiCheck.app under
# .build/package-check, with the identifier dev.kyosei.apple.check and the check channel
# compiled in, for the harness in check/. It is built for this machine's architecture in a
# build directory of its own, .build/check, so it neither changes .build/package nor shares
# object files with the app's build. It is signed ad hoc, or with KYOSEI_SIGN_IDENTITY, and
# is never notarized or given the provisioning profile: the command stops if a variable
# that asks for either is set.
# Its Info.plist sets LSUIElement, so that it starts as an accessory app, with no Dock icon,
# and does not come in front of the app a person is using (KYO-945).
set -eu
cd "$(dirname "$0")"

MARKER=KYOSEI_CHECK_SOCKET

if [ "${1:-}" = check ]; then
  for name in KYOSEI_NOTARY_KEY KYOSEI_NOTARY_KEY_ID KYOSEI_NOTARY_ISSUER KYOSEI_PROVISIONING_PROFILE; do
    if eval "[ -n \"\${$name:-}\" ]"; then
      echo "package.sh check: $name is set; a check build is never notarized and takes no provisioning profile. Unset it and run again." >&2
      exit 2
    fi
  done
  OUT=.build/package-check
  APP="$OUT/KyoseiCheck.app"
  # The renderer is built into the sources' resources, which the app's build shares.
  (cd web && { [ -d node_modules ] || npm ci --no-fund --no-audit; } && npm run build)
  swift build -c release --scratch-path .build/check -Xswiftc -DKYOSEI_CHECK
  PRODUCTS=$(swift build -c release --scratch-path .build/check -Xswiftc -DKYOSEI_CHECK --show-bin-path)
  if ! grep -q "$MARKER" "$PRODUCTS/KyoseiMac"; then
    echo "package.sh check: the executable built holds no $MARKER, so it is not a check build" >&2
    exit 1
  fi
  rm -rf "$OUT"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cp "$PRODUCTS/KyoseiMac" "$APP/Contents/MacOS/KyoseiCheck"
  cp -R "$PRODUCTS/KyoseiApple_KyoseiMac.bundle" "$APP/Contents/Resources/"
  cp Packaging/Info.plist "$APP/Contents/Info.plist"
  cp Packaging/Kyosei.icns "$APP/Contents/Resources/Kyosei.icns"
  /usr/libexec/PlistBuddy \
    -c 'Set :CFBundleIdentifier dev.kyosei.apple.check' \
    -c 'Set :CFBundleExecutable KyoseiCheck' \
    -c 'Set :CFBundleName KyoseiCheck' \
    -c 'Add :LSUIElement bool true' "$APP/Contents/Info.plist"
  if [ -n "${KYOSEI_SIGN_IDENTITY:-}" ]; then
    codesign --force --options runtime --sign "$KYOSEI_SIGN_IDENTITY" "$APP"
  else
    codesign --force --sign - "$APP"
  fi
  echo "$APP"
  exit 0
elif [ -n "${1:-}" ]; then
  echo "package.sh: takes no argument, or check for the check build" >&2
  exit 2
fi

OUT=.build/package
APP="$OUT/Kyosei.app"

(cd web && npm ci --no-fund --no-audit && npm run build)
swift build -c release --arch arm64 --arch x86_64
PRODUCTS=$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)

# The check channel is compiled only into a check build (KYO-944). An executable that holds
# its marker is not packaged as the app.
if grep -q "$MARKER" "$PRODUCTS/KyoseiMac"; then
  echo "package.sh: the executable built holds $MARKER, the check channel's marker; it is not packaged as Kyosei.app" >&2
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PRODUCTS/KyoseiMac" "$APP/Contents/MacOS/Kyosei"
cp -R "$PRODUCTS/KyoseiApple_KyoseiMac.bundle" "$APP/Contents/Resources/"
cp Packaging/Info.plist "$APP/Contents/Info.plist"
cp Packaging/Kyosei.icns "$APP/Contents/Resources/Kyosei.icns"

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
