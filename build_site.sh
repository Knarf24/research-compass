#!/bin/sh
# Puts the web page, the Python logic and this week's data together in _site/ (what GitHub Pages serves).
set -e
rm -rf _site
mkdir -p _site
cp -r web/. _site/
touch _site/.nojekyll
python3 scripts/share_pages.py _site
# Tell the service worker which Pyodide version this is, so every app on the site can share one saved copy.
PYODIDE_VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' web/pyodide/package.json | head -1)
[ -n "$PYODIDE_VERSION" ] && sed -i "s/^const PYODIDE_VERSION = \"dev\";/const PYODIDE_VERSION = \"$PYODIDE_VERSION\";/" _site/sw.js
true
