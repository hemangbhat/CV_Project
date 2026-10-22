#!/bin/sh
# Build a guide PDF (pandoc -> HTML -> headless Chromium).
# Usage: sh scripts/build_master_pdf.sh [MASTER_GUIDE|MASTER_GUIDE_HINGLISH]
set -e
NAME=${1:-MASTER_GUIDE}
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
sed 's#^\\newpage$#<div class="pagebreak"></div>#' docs/$NAME.md > "$TMP/guide.md"
pandoc "$TMP/guide.md" -f markdown -t html5 --standalone --mathml --toc --toc-depth=2 \
  --resource-path=docs --embed-resources --css scripts/master_guide.css \
  --metadata lang=en -o "$TMP/guide.html"
CHROME=${CHROME:-$(ls -d /opt/pw-browsers/chromium-*/chrome-linux/chrome 2>/dev/null | head -1)}
"$CHROME" --headless --no-sandbox --disable-gpu --no-pdf-header-footer \
  --print-to-pdf=docs/$NAME.pdf "file://$TMP/guide.html" 2>/dev/null
rm -rf "$TMP"
echo "wrote docs/$NAME.pdf"
