# docs/

Files that are fetched from outside the repository **at a pinned commit**, so they must
exist in the release commit the Hermes plugin catalog points at. Nothing here is
installed with the plugin.

## The catalog banner

`catalog-banner.png` is the 2:1 image on the plugin-catalog card and in Hermes Desktop
(1200×600). It is rendered from `catalog-banner.html`, a single self-contained page in
the palette from `marketing/DESIGN.md`. Edit the HTML, not the PNG:

```bash
make banner            # renders docs/catalog-banner.png with headless Chrome/Chromium
make banner SCALE=2    # 2400×1200 for a crisper card on high-DPI screens (still 2:1)
```

The target autodetects `google-chrome`, `chromium`, `chromium-browser`, `chrome` on the
PATH and the macOS Google Chrome app; pass `CHROME=/path/to/binary` otherwise. Open
`docs/catalog-banner.html` in a browser at exactly 1200×600 to preview while editing.
Fonts fall back down the `--mono` / `--sans` stacks, so a render on another OS can
differ slightly from the committed PNG; commit the PNG your `make banner` produced
together with the HTML change.

Constraints from the catalog (`plugin-catalog/README.md` in hermes-agent): 2:1 aspect,
other shapes are centre-cropped; keep it under about 200 KB; the entry references it as
`https://raw.githubusercontent.com/briancaffey/hermes-otel/<sha>/docs/catalog-banner.png`,
which `scripts/render_catalog_entry.py` fills in for each release.
