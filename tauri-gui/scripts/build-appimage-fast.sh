#!/usr/bin/env bash
set -euo pipefail

tauri_root="$(cd "$(dirname "$0")/.." && pwd)"
repo_root="$(cd "$tauri_root/.." && pwd)"
appimage_dir="$tauri_root/src-tauri/target/release/bundle/appimage"
appdir="$appimage_dir/TaxonDBBuilder.AppDir"
gui_binary="$tauri_root/src-tauri/target/release/taxondbbuilder-gui"
sidecar_binary="$repo_root/dist/taxondbbuilder"

if [[ ! -d "$appdir" ]]; then
  echo "AppDir cache missing; run the regular AppImage build once first." >&2
  exit 1
fi

cd "$tauri_root"
npm run tauri -- build --no-bundle

appdir_gui="$appdir/usr/bin/taxondbbuilder-gui"
gui_rpath="$(patchelf --print-rpath "$appdir_gui")"
cp "$gui_binary" "$appdir_gui"
patchelf --set-rpath "$gui_rpath" "$appdir_gui"
cp "$sidecar_binary" "$appdir/usr/bin/taxondbbuilder"

version="$(node -p "JSON.parse(require('fs').readFileSync('src-tauri/tauri.conf.json')).version")"
output="$appimage_dir/TaxonDBBuilder_${version}_amd64.AppImage"
temporary_output="$appimage_dir/.TaxonDBBuilder_${version}_amd64.$$.AppImage"
plugin_appimage="${TAURI_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/tauri}/linuxdeploy-plugin-appimage.AppImage"
tool_cache="$appimage_dir/.appimagetool"

if [[ ! -x "$tool_cache/squashfs-root/usr/bin/appimagetool" ]]; then
  if [[ ! -f "$plugin_appimage" ]]; then
    echo "Tauri's cached linuxdeploy AppImage tool is missing: $plugin_appimage" >&2
    exit 1
  fi
  mkdir -p "$tool_cache"
  (cd "$tool_cache" && "$plugin_appimage" --appimage-extract >/dev/null)
fi

appimagetool="$tool_cache/squashfs-root/usr/bin/appimagetool"
runtime_args=()
if [[ -n "${LDAI_RUNTIME_FILE:-}" ]]; then
  runtime_args=(--runtime-file "$LDAI_RUNTIME_FILE")
fi

trap 'rm -f "$temporary_output"' EXIT
ARCH=x86_64 "$appimagetool" --no-appstream --comp zstd \
  --mksquashfs-opt -Xcompression-level --mksquashfs-opt 1 \
  "${runtime_args[@]}" "$appdir" "$temporary_output"
mv -f "$temporary_output" "$output"
echo "Built AppImage using cached AppDir: $output"
