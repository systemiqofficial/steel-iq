# Vendor Library Licenses

This directory contains license files for all third-party libraries that are bundled with STEEL-IQ for offline capability.

## Included Licenses

| Library | Version | License Type | File |
|---------|---------|--------------|------|
| Bootstrap | 5.3.0 | MIT License | Bootstrap-LICENSE.txt |
| Font Awesome Free | 6.0.0 | Font Awesome Free License | FontAwesome-LICENSE.txt |
| Highlight.js | 11.9.0 | BSD 3-Clause License | Highlightjs-LICENSE.txt |
| Deck.gl | 8.9.35 | MIT License | Deckgl-LICENSE.txt |

## Libraries NOT Included

**Mapbox GL JS** (v2.15.0) - **NOT BUNDLED**
- Reason: Mapbox GL JS v2.x uses a proprietary license that prohibits bundling/redistribution
- Solution: Mapbox GL remains CDN-hosted on unpkg.com (intentional exception)
- Impact: Geospatial visualizations require internet connection; core app works offline
- Documentation: See `specs/2025-10-13_no_cdn.md` and `specs/2025-10-13_no_cdn_CARVEOUT.md`

## License Compliance

All bundled libraries have licenses that explicitly permit:
1. ✅ Redistribution of source and compiled code
2. ✅ Bundling within applications
3. ✅ Commercial use

License files are included to comply with attribution requirements.

## Updates

When updating vendored libraries, ensure:
1. The license file is updated to match the new version
2. The license terms still permit bundling and redistribution
3. The version number is updated in this README
4. Attribution requirements are met

## References

- Bootstrap: https://github.com/twbs/bootstrap
- Font Awesome: https://github.com/FortAwesome/Font-Awesome
- Highlight.js: https://github.com/highlightjs/highlight.js
- Deck.gl: https://github.com/visgl/deck.gl
- Mapbox GL JS: https://github.com/mapbox/mapbox-gl-js (CDN only)
