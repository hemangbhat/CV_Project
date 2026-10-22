#!/bin/sh
# Build docs/MASTER_GUIDE.pdf from docs/MASTER_GUIDE.md (pandoc -> HTML -> headless Chromium).
set -e
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
sed 's#^\\newpage$#<div class="pagebreak"></div>#' docs/MASTER_GUIDE.md > "$TMP/guide.md"
pandoc "$TMP/guide.md" -f markdown -t html5 --standalone --mathml --toc --toc-depth=2 \
  --resource-path=docs --embed-resources --css scripts/master_guide.css \
  --metadata lang=en -o "$TMP/guide.html"
CHROME=${CHROME:-$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome 2>/dev/null | head -1)}
"$CHROME" --headless --no-sandbox --disable-gpu --no-pdf-header-footer \
  --print-to-pdf=docs/MASTER_GUIDE.pdf "file://$TMP/guide.html" 2>/dev/null
rm -rf "$TMP"
echo "wrote docs/MASTER_GUIDE.pdf"
