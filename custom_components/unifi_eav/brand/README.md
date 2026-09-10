# Brand images (replace the placeholders)

Home Assistant 2026.3+ serves these files itself (`/api/brands/integration/unifi_eav/...`); no pull request to `home-assistant/brands` is needed or accepted for custom integrations any more. Drop the final artwork in here with the exact names below, keep them real PNGs (not renamed JPEGs), then bump `manifest.json` version and cut a release.

| File | Size | Notes |
|---|---|---|
| `icon.png` | 256 x 256 | square, transparent background preferred |
| `icon@2x.png` | 512 x 512 | same artwork, 2x |
| `logo.png` | max 256 px tall, any width | wordmark or wide logo; may be the icon again |
| `logo@2x.png` | 2x of `logo.png` | |
| `dark_icon.png`, `dark_icon@2x.png`, `dark_logo.png`, `dark_logo@2x.png` | as above | optional variants for dark theme; omitted = light version is used |

Check a file is a true PNG: the first bytes must be `89 50 4E 47` (`file icon.png` should say "PNG image data"). Exporting from a design tool as PNG with alpha is the safest path.

Known HACS limitation (Sept 2026): the HACS *store/dashboard* list still fetches icons from the legacy CDN, so it shows a generic placeholder for custom integrations that only ship local brand files. The integration pages inside Home Assistant itself show the icon correctly. Tracked in hacs/integration#5171 / #5223.
