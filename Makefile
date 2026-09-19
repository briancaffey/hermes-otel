# Developer conveniences. The CI gate itself is documented in CONTRIBUTING.md.

# Headless Chrome/Chromium renders docs/catalog-banner.html to the PNG the
# plugin-catalog card shows (2:1, 1200x600). Override CHROME if autodetection
# misses your binary, SCALE for a 2x render (2400x1200, also 2:1).
CHROME ?= $(shell command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null || command -v chrome 2>/dev/null || ls "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" 2>/dev/null)
SCALE  ?= 1

.PHONY: banner
banner:
	@test -n "$(CHROME)" || { echo "no Chrome/Chromium found; set CHROME=/path/to/chrome"; exit 1; }
	"$(CHROME)" --headless=new --disable-gpu --hide-scrollbars \
	  --force-device-scale-factor=$(SCALE) --window-size=1200,600 \
	  --screenshot="$(abspath docs/catalog-banner.png)" "file://$(abspath docs/catalog-banner.html)" >/dev/null 2>&1
	@python3 -c 'import struct,sys; d=open("docs/catalog-banner.png","rb").read(24); w,h=struct.unpack(">II", d[16:24]); print("docs/catalog-banner.png:", w, "x", h, "px,", len(open("docs/catalog-banner.png","rb").read())//1024, "KB"); sys.exit(0 if w==2*h else 1)'
