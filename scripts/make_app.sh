#!/usr/bin/env bash
#
# Assemble Builder.app from the SwiftPM build.
#
# There is no Xcode project, on purpose. A macOS .app is a directory with an Info.plist
# and a binary in the right place — hand-maintaining a pbxproj to produce that buys
# nothing and costs a file format nobody can review in a diff. `swift build` produces the
# executable; this puts a bundle around it.
#
# Usage:
#   scripts/make_app.sh                    debug build, unsigned, runs locally
#   scripts/make_app.sh --release          release build
#   scripts/make_app.sh --release --sign   release + Developer ID + hardened runtime
#
# Notarization is a separate step and needs the WP-0 credentials:
#   xcrun notarytool submit build/Builder.dmg --keychain-profile builder-notary --wait
#   xcrun stapler staple build/Builder.app

set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG="debug"
SIGN=0
for arg in "$@"; do
  case "$arg" in
    --release) CONFIG="release" ;;
    --sign) SIGN=1 ;;
  esac
done

PKG="Packages/BuilderKit"
APP="build/Builder.app"
BUNDLE_ID="com.vedantlbhatt.Builder.Mac"
VERSION="0.1.0"
BUILD_NUMBER="$(git rev-list --count HEAD 2>/dev/null || echo 1)"

echo "==> building ($CONFIG)"
swift build --package-path "$PKG" -c "$CONFIG" --product BuilderMac

BIN="$PKG/.build/$CONFIG/BuilderMac"
[ -x "$BIN" ] || { echo "no binary at $BIN"; exit 1; }

echo "==> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Builder"

# SwiftPM emits resource bundles (the .sql schema files) next to the binary. They have to
# travel with the app or the first launch cannot create its own database.
for bundle in "$PKG/.build/$CONFIG"/*.bundle; do
  [ -e "$bundle" ] && cp -R "$bundle" "$APP/Contents/Resources/"
done

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Builder</string>
  <key>CFBundleDisplayName</key><string>Builder</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>Builder</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD_NUMBER</string>
  <key>LSMinimumSystemVersion</key><string>15.0</string>

  <!-- Menu bar only: no Dock icon, no app switcher entry. A tracker that takes a Dock
       slot is a tracker people quit. -->
  <key>LSUIElement</key><true/>

  <key>NSHumanReadableCopyright</key><string>Builder</string>
  <key>ITSAppUsesNonExemptEncryption</key><false/>
</dict>
</plist>
PLIST

if [ "$SIGN" = "1" ]; then
  IDENTITY="$(security find-identity -v -p codesigning \
    | grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)"/\1/')"

  if [ -z "$IDENTITY" ]; then
    cat <<'MSG'
==> no Developer ID Application certificate found.

This is the one hard external dependency in the project and only the team's Account
Holder can create it:

  Xcode -> Settings -> Accounts -> your team -> Manage Certificates -> + ->
  Developer ID Application

Export the .p12 with a password into a password manager THE SAME MINUTE. There are five
per team for the lifetime of the team and the private key is unrecoverable.

Until then the app still runs locally unsigned; it just cannot be distributed.
MSG
    exit 1
  fi

  echo "==> signing as $IDENTITY"
  # --timestamp and hardened runtime are both required for notarization, and the failure
  # mode without them is an opaque rejection hours later.
  # Never --deep: it silently re-signs nested content with the wrong options.
  codesign --force --options runtime --timestamp \
    --sign "$IDENTITY" "$APP/Contents/MacOS/Builder"
  codesign --force --options runtime --timestamp \
    --sign "$IDENTITY" "$APP"

  echo "==> verifying"
  codesign --verify --deep --strict --verbose=2 "$APP"
  # Release builds MUST NOT carry get-task-allow — it is the single most common
  # notarization rejection, and it is invisible unless you look.
  if codesign -d --entitlements - "$APP" 2>/dev/null | grep -q "get-task-allow"; then
    echo "FAIL: get-task-allow present in a signed build; notarization will reject this."
    exit 1
  fi
fi

echo ""
echo "==> $APP"
echo "    open $APP        # run it"
echo "    codesign -dv --verbose=4 $APP"
