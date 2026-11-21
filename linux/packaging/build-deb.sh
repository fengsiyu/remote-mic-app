#!/usr/bin/env bash
set -euo pipefail

export PYTHONUNBUFFERED=1

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${SAYALL_LINUX_VERSION:-0.1.0}"
ARCH="${SAYALL_LINUX_ARCH:-amd64}"
if [[ "$ARCH" != "amd64" ]]; then
  printf 'unsupported Linux package architecture: %s (only amd64 is currently supported)\n' "$ARCH" >&2
  exit 1
fi
HOST_ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
if [[ "$HOST_ARCH" != "amd64" ]]; then
  printf 'sayall-linux must be built on an amd64 host (found %s)\n' "${HOST_ARCH:-unknown}" >&2
  exit 1
fi
PACKAGE_NAME="sayall-linux"
BUILD_ROOT="$ROOT/build/linux-deb"
PACKAGE_ROOT="$BUILD_ROOT/${PACKAGE_NAME}_${VERSION}_${ARCH}"
OUTPUT_DIR="$ROOT/dist"
OUTPUT="$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

command -v dpkg-deb >/dev/null
command -v desktop-file-validate >/dev/null
command -v timeout >/dev/null

printf 'Running Linux unit tests...\n'
timeout 120s env PYTHONPATH="$ROOT/linux${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -u -m unittest discover -s "$ROOT/linux/tests" -t "$ROOT/linux"
printf 'Compiling Python sources...\n'
python3 -u -m compileall -q "$ROOT/linux/sayall_linux"

rm -rf -- "$PACKAGE_ROOT"
mkdir -p \
  "$PACKAGE_ROOT/DEBIAN" \
  "$PACKAGE_ROOT/usr/bin" \
  "$PACKAGE_ROOT/usr/lib/sayall-linux" \
  "$PACKAGE_ROOT/usr/share/applications" \
  "$PACKAGE_ROOT/usr/share/icons/hicolor/256x256/apps" \
  "$PACKAGE_ROOT/usr/share/icons/hicolor/512x512/apps" \
  "$PACKAGE_ROOT/usr/share/doc/sayall-linux" \
  "$PACKAGE_ROOT/usr/lib/udev/rules.d" \
  "$PACKAGE_ROOT/usr/lib/modules-load.d" \
  "$PACKAGE_ROOT/etc/xdg/autostart" \
  "$OUTPUT_DIR"

install -Dm755 "$ROOT/linux/bin/sayall-linux" "$PACKAGE_ROOT/usr/bin/sayall-linux"
cp -a "$ROOT/linux/sayall_linux" "$PACKAGE_ROOT/usr/lib/sayall-linux/"
find "$PACKAGE_ROOT/usr/lib/sayall-linux" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$PACKAGE_ROOT/usr/lib/sayall-linux" -type f -name '*.pyc' -delete

install -Dm644 "$ROOT/linux/packaging/sayall-linux.desktop" "$PACKAGE_ROOT/usr/share/applications/sayall-linux.desktop"
install -Dm644 "$ROOT/linux/packaging/sayall-linux-autostart.desktop" "$PACKAGE_ROOT/etc/xdg/autostart/sayall-linux.desktop"
install -Dm644 "$ROOT/Resources/AppIcon.png" "$PACKAGE_ROOT/usr/share/icons/hicolor/256x256/apps/sayall-linux.png"
install -Dm644 "$ROOT/Resources/AppIcon.png" "$PACKAGE_ROOT/usr/share/icons/hicolor/512x512/apps/sayall-linux.png"
install -Dm644 "$ROOT/LICENSE.md" "$PACKAGE_ROOT/usr/share/doc/sayall-linux/copyright"
install -Dm644 "$ROOT/linux/packaging/99-sayall-input.rules" "$PACKAGE_ROOT/usr/lib/udev/rules.d/99-sayall-input.rules"
install -Dm644 "$ROOT/linux/packaging/sayall-uinput.conf" "$PACKAGE_ROOT/usr/lib/modules-load.d/sayall-uinput.conf"

cat > "$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, python3-dbus, bluez, pulseaudio-utils
Recommends: gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1, gnome-control-center | blueman
Maintainer: SayAll Maintainers
Description: SayAll wireless voice remote for Linux
 Connects Xiaomi RC001 and RC003 Bluetooth remotes, routes ATVV voice audio to
 an automatically managed SayAll virtual microphone, and maps remote buttons
 through the Linux input subsystem.
EOF

cat > "$PACKAGE_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
modprobe uinput 2>/dev/null || true
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules >/dev/null 2>&1 || true
  udevadm trigger --subsystem-match=misc --sysname-match=uinput >/dev/null 2>&1 || true
  udevadm trigger --subsystem-match=input >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 755 "$PACKAGE_ROOT/DEBIAN/postinst"

desktop-file-validate "$PACKAGE_ROOT/usr/share/applications/sayall-linux.desktop"
desktop-file-validate "$PACKAGE_ROOT/etc/xdg/autostart/sayall-linux.desktop"
printf 'Building deb package...\n'
timeout 120s fakeroot dpkg-deb --build "$PACKAGE_ROOT" "$OUTPUT" >/dev/null
printf '%s\n' "$OUTPUT"
