#!/bin/sh
# Puts the web page, the Python logic and this week's data together in _site/ (what GitHub Pages serves).
set -e
rm -rf _site
mkdir -p _site
cp -r web/. _site/
touch _site/.nojekyll
