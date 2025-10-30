"""Tests for vendor static files - ensuring offline capability with CDN exceptions.

This test suite verifies:
1. All vendor static files are available (Bootstrap, Font Awesome, Highlight.js, Deck.gl)
2. Templates don't reference disallowed CDNs (cdn.jsdelivr.net, cdnjs.cloudflare.com)
3. Mapbox GL JS stays on CDN (intentional exception due to licensing)

See specs/2025-10-13_no_cdn.md for full specification.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders


def test_bootstrap_css_exists():
    """Test that Bootstrap CSS is available as vendor static file."""
    result = finders.find("vendor/bootstrap-5.3.0/css/bootstrap.min.css")
    assert result is not None, "Bootstrap CSS not found - check vendor assets"


def test_bootstrap_js_exists():
    """Test that Bootstrap JS bundle is available as vendor static file."""
    result = finders.find("vendor/bootstrap-5.3.0/js/bootstrap.bundle.min.js")
    assert result is not None, "Bootstrap JS not found - check vendor assets"


def test_fontawesome_css_exists():
    """Test that Font Awesome CSS is available as vendor static file."""
    result = finders.find("vendor/fontawesome-6.0.0/css/all.min.css")
    assert result is not None, "Font Awesome CSS not found - check vendor assets"


def test_fontawesome_fonts_exist():
    """Test that Font Awesome webfonts are available."""
    # Check for at least one font file (woff2 format is most common)
    font_patterns = [
        "vendor/fontawesome-6.0.0/webfonts/fa-solid-900.woff2",
        "vendor/fontawesome-6.0.0/webfonts/fa-regular-400.woff2",
        "vendor/fontawesome-6.0.0/webfonts/fa-brands-400.woff2",
    ]

    found_fonts = []
    for pattern in font_patterns:
        result = finders.find(pattern)
        if result:
            found_fonts.append(pattern)

    assert len(found_fonts) > 0, "No Font Awesome fonts found - check vendor assets"


def test_custom_fonts_css_exists():
    """Test that custom fonts CSS is available (Saira font declarations)."""
    result = finders.find("steeloweb/css/fonts.css")
    assert result is not None, "Custom fonts.css not found - check steeloweb static files"


def test_custom_main_css_exists():
    """Test that custom main CSS is available (global typography, navbar, buttons)."""
    result = finders.find("steeloweb/css/main.css")
    assert result is not None, "Custom main.css not found - check steeloweb static files"


def test_saira_font_exists():
    """Test that Saira font file is available."""
    result = finders.find("steeloweb/fonts/saira/Saira-latin-variable.woff2")
    assert result is not None, "Saira font not found - check steeloweb static files"


def test_steel_iq_logo_exists():
    """Test that Steel-IQ signet SVG logos are available."""
    light_logo = finders.find("steeloweb/images/Steel-IQ-signet-light.svg")
    dark_logo = finders.find("steeloweb/images/Steel-IQ-signet-dark.svg")

    assert light_logo is not None, "Steel-IQ signet light logo not found"
    assert dark_logo is not None, "Steel-IQ signet dark logo not found"


def test_highlightjs_exists():
    """Test that Highlight.js core library is available as vendor static file."""
    result = finders.find("vendor/highlightjs-11.9.0/highlight.min.js")
    assert result is not None, "Highlight.js not found - check vendor assets"


def test_highlightjs_json_language_exists():
    """Test that Highlight.js JSON language support is available."""
    result = finders.find("vendor/highlightjs-11.9.0/languages/json.min.js")
    assert result is not None, "Highlight.js JSON language not found - check vendor assets"


def test_highlightjs_css_exists():
    """Test that Highlight.js CSS theme is available."""
    result = finders.find("vendor/highlightjs-11.9.0/styles/github.min.css")
    assert result is not None, "Highlight.js CSS theme not found - check vendor assets"


def test_deckgl_exists():
    """Test that Deck.gl library is available as vendor static file."""
    result = finders.find("vendor/mapping-libs/deck.gl@8.9.35/dist.min.js")
    assert result is not None, "Deck.gl not found - check vendor assets"


def test_mapbox_not_vendored():
    """Test that Mapbox GL is NOT vendored (should stay on CDN due to licensing)."""
    # Check that no Mapbox GL files are in vendor directory
    vendor_dir = Path(settings.BASE_DIR.parent / "steeloweb" / "static" / "vendor")

    if vendor_dir.exists():
        # Search for any files with "mapbox" in the name
        mapbox_files = list(vendor_dir.rglob("*mapbox*"))
        assert len(mapbox_files) == 0, (
            f"Mapbox GL found in vendor directory: {mapbox_files}. "
            "Mapbox GL must stay on CDN due to licensing constraints. "
            "See specs/2025-10-13_no_cdn.md"
        )


# CDN reference patterns to check for
DISALLOWED_CDN_PATTERNS = [
    r"cdn\.jsdelivr\.net",  # Bootstrap, other libraries
    r"cdnjs\.cloudflare\.com",  # Font Awesome, Highlight.js
]

# CDN patterns that are explicitly allowed (exceptions)
ALLOWED_CDN_PATTERNS = [
    r"unpkg\.com/mapbox-gl@",  # Mapbox GL JS - licensing exception
]


def test_base_template_no_disallowed_cdns():
    """Test that base.html doesn't reference disallowed CDN URLs.

    Allowed exception: Mapbox GL JS on unpkg.com (licensing constraint).
    """
    template_path = Path(settings.BASE_DIR.parent / "steeloweb" / "templates" / "base.html")
    assert template_path.exists(), f"Template not found: {template_path}"

    content = template_path.read_text()

    # Check for disallowed CDN patterns
    for pattern in DISALLOWED_CDN_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        assert len(matches) == 0, (
            f"Found disallowed CDN reference in base.html: {pattern}. "
            "All libraries except Mapbox GL should be vendored locally. "
            "See specs/2025-10-13_no_cdn.md"
        )


def test_prepared_file_view_template_no_disallowed_cdns():
    """Test that prepared_file_view.html doesn't reference disallowed CDN URLs."""
    template_path = Path(settings.BASE_DIR.parent / "steeloweb" / "templates" / "steeloweb" / "prepared_file_view.html")
    assert template_path.exists(), f"Template not found: {template_path}"

    content = template_path.read_text()

    # Check for disallowed CDN patterns
    for pattern in DISALLOWED_CDN_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        assert len(matches) == 0, (
            f"Found disallowed CDN reference in prepared_file_view.html: {pattern}. "
            "All libraries except Mapbox GL should be vendored locally. "
            "See specs/2025-10-13_no_cdn.md"
        )


def test_mapbox_cdn_exception_in_plotting():
    """Test that Mapbox GL stays on CDN in plotting.py (intentional exception).

    This is an allowed exception due to Mapbox GL JS v2.x licensing constraints.
    The license prohibits bundling/redistribution but allows CDN usage.
    """
    plotting_path = Path(settings.BASE_DIR.parent / "steelo" / "utilities" / "plotting.py")
    assert plotting_path.exists(), f"File not found: {plotting_path}"

    content = plotting_path.read_text()

    # Verify Mapbox GL is on CDN (whitelisted)
    mapbox_pattern = r"unpkg\.com/mapbox-gl@2\.15\.0"
    matches = re.findall(mapbox_pattern, content, re.IGNORECASE)

    assert len(matches) > 0, (
        "Mapbox GL JS not found on CDN in plotting.py. "
        "Mapbox GL MUST stay on CDN due to licensing constraints. "
        "See specs/2025-10-13_no_cdn_CARVEOUT.md"
    )


def test_mapbox_cdn_exception_documented():
    """Test that the Mapbox GL CDN exception is properly documented in plotting.py."""
    plotting_path = Path(settings.BASE_DIR.parent / "steelo" / "utilities" / "plotting.py")
    assert plotting_path.exists(), f"File not found: {plotting_path}"

    content = plotting_path.read_text()

    # Look for documentation comment about the CDN exception
    exception_keywords = ["EXCEPTION", "CDN", "licensing"]

    found_keywords = sum(1 for keyword in exception_keywords if keyword.lower() in content.lower())

    assert found_keywords >= 2, (
        "Mapbox GL CDN exception should be documented in plotting.py with comment. "
        "Should mention 'EXCEPTION' and 'licensing'. "
        "See specs/2025-10-13_no_cdn.md"
    )


def test_deckgl_uses_local_vendor():
    """Test that Deck.gl is copied to output directory for standalone HTML files.

    The plotting module should copy deck.gl from vendor directory to the output
    directory so standalone HTML files (opened with file:// protocol) can load it.
    """
    plotting_path = Path(settings.BASE_DIR.parent / "steelo" / "utilities" / "plotting.py")
    assert plotting_path.exists(), f"File not found: {plotting_path}"

    content = plotting_path.read_text()

    # Verify that _copy_deckgl_to_output_dir function exists
    assert "_copy_deckgl_to_output_dir" in content, (
        "plotting.py should have _copy_deckgl_to_output_dir function to copy deck.gl "
        "for standalone HTML files (file:// protocol support)"
    )

    # Verify the function copies from vendor directory
    vendor_path_pattern = r"steeloweb.*static.*vendor.*deck\.gl"
    assert re.search(vendor_path_pattern, content), (
        "Should reference vendor deck.gl path for copying to output directory"
    )

    # Verify HTML uses deckgl_path variable (not hardcoded /static/ path)
    # The path should be dynamic based on whether vendor file is available
    assert 'src="{deckgl_path}"' in content, (
        "HTML should use {deckgl_path} variable for deck.gl, not hardcoded /static/ path. "
        "This allows standalone HTML files to work with file:// protocol."
    )


@pytest.mark.parametrize(
    "template_name,expected_vendor_refs",
    [
        (
            "base.html",
            [
                "vendor/bootstrap-5.3.0/css/bootstrap.min.css",
                "vendor/fontawesome-6.0.0/css/all.min.css",
                "vendor/bootstrap-5.3.0/js/bootstrap.bundle.min.js",
                "steeloweb/css/fonts.css",  # Custom Saira font declarations
                "steeloweb/css/main.css",  # Custom typography and styles
                "steeloweb/images/Steel-IQ-signet-light.svg",  # Logo
            ],
        ),
        (
            "steeloweb/prepared_file_view.html",
            [
                "vendor/highlightjs-11.9.0/styles/github.min.css",
                "vendor/highlightjs-11.9.0/highlight.min.js",
                "vendor/highlightjs-11.9.0/languages/json.min.js",
            ],
        ),
    ],
)
def test_templates_use_vendor_static_paths(template_name, expected_vendor_refs):
    """Test that templates reference local vendor static paths and custom CSS/assets.

    This parameterized test checks that each template uses the correct paths for:
    - Vendor assets (Bootstrap, Font Awesome, Highlight.js)
    - Custom CSS (fonts.css, main.css)
    - Custom images (Steel-IQ logo)
    """
    template_path = Path(settings.BASE_DIR.parent / "steeloweb" / "templates" / template_name)
    assert template_path.exists(), f"Template not found: {template_path}"

    content = template_path.read_text()

    for vendor_ref in expected_vendor_refs:
        assert vendor_ref in content, (
            f"Template {template_name} should reference {vendor_ref}. "
            "All assets should be served locally for offline capability."
        )
