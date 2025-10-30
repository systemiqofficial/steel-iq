"""
Custom storage backends for Django static files.
"""

from django.contrib.staticfiles.storage import ManifestFilesMixin
from whitenoise.storage import CompressedStaticFilesStorage


class NonStrictCompressedStaticFilesStorage(ManifestFilesMixin, CompressedStaticFilesStorage):
    """
    Whitenoise storage with compression, manifest hashing, and lenient CSS validation.

    This combines the benefits of:
    - Compression (gzip/brotli via whitenoise)
    - Cache-busting via hashed filenames (ManifestFilesMixin)
    - Lenient validation that tolerates missing font format references

    We use this for the Electron app build where:
    - Font Awesome CSS references multiple formats (.woff2, .ttf)
    - We only vendor .woff2 (sufficient for modern Chromium)
    - Strict validation would fail on missing .ttf files

    Overrides hashed_name() to return the original filename when a file is missing,
    instead of raising ValueError. This allows CSS to reference files that don't exist
    (like .ttf fallback fonts) without breaking collectstatic.
    """

    manifest_strict = False

    def hashed_name(self, name, content=None, filename=None):
        """
        Return hashed name if file exists, otherwise return original name.

        This prevents ValueError when CSS references missing files (like .ttf fonts).
        The browser will try to load the missing file, get a 404, and fall back to
        the .woff2 file that actually exists.
        """
        try:
            return super().hashed_name(name, content, filename)
        except ValueError:
            # File not found - return original name without hashing
            # This is safe for Font Awesome .ttf files that we don't vendor
            return name
