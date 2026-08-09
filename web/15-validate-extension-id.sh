#!/bin/sh
set -eu

extension_id=${VIBECANVAS_BROWSER_EXTENSION_ID:-}
case "$extension_id" in
  *[!a-p]*)
    echo "VIBECANVAS_BROWSER_EXTENSION_ID must be a 32-character Chrome extension id" >&2
    exit 1
    ;;
esac
if [ "${#extension_id}" -ne 32 ]; then
  echo "VIBECANVAS_BROWSER_EXTENSION_ID must be a 32-character Chrome extension id" >&2
  exit 1
fi
