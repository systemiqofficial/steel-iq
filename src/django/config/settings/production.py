"""
Production Configurations

- Uses SQLite for database
- Uses file-based cache
"""

# Import the base settings
from .base import *  # noqa

DEBUG = False

# SECRET CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
# Raises ImproperlyConfigured exception if DJANGO_SECRET_KEY not in os.environ
SECRET_KEY = env("DJANGO_SECRET_KEY")  # noqa


# This ensures that Django will be able to detect a secure connection
# properly on Heroku.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Use Whitenoise to serve static files
# See: https://whitenoise.readthedocs.io/
WHITENOISE_MIDDLEWARE = [
    "whitenoise.middleware.WhiteNoiseMiddleware",
]
MIDDLEWARE = WHITENOISE_MIDDLEWARE + MIDDLEWARE  # noqa

# SECURITY CONFIGURATION
# ------------------------------------------------------------------------------
# See https://docs.djangoproject.com/en/dev/ref/middleware/#module-django.middleware.security
# and https://docs.djangoproject.com/en/dev/howto/deployment/checklist/#run-manage-py-check-deploy

# set this to 60 seconds and then to 518400 when you can prove it works
SECURE_HSTS_SECONDS = 60
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(  # noqa
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
)
SECURE_CONTENT_TYPE_NOSNIFF = env.bool(  # noqa
    "DJANGO_SECURE_CONTENT_TYPE_NOSNIFF", default=True
)
SECURE_BROWSER_XSS_FILTER = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)  # noqa
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_PRELOAD = True

# SITE CONFIGURATION
# ------------------------------------------------------------------------------
# Hosts/domain names that are valid for this site
# See https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = env.list(  # noqa
    "DJANGO_ALLOWED_HOSTS",
    default=[
        "steeloweb.django-cast.com",
    ],
)
# END SITE CONFIGURATION

# INSTALLED_APPS += [  # noqa
#     "granian",
# ]


# Static Assets
# ------------------------
# ⚠️ CRITICAL: Use NonStrictCompressedStaticFilesStorage for Electron builds
#
# DO NOT switch to strict manifest storage (e.g., CompressedManifestStaticFilesStorage with
# manifest_strict=True). This will break collectstatic because Font Awesome CSS references
# .ttf files we don't vendor (we only vendor .woff2, sufficient for modern Chromium).
#
# Current storage backend (NonStrictCompressedStaticFilesStorage) provides:
# - ✅ Compression (gzip/brotli via whitenoise)
# - ✅ Cache-busting via hashed filenames (ManifestFilesMixin)
# - ✅ Lenient CSS validation (manifest_strict=False tolerates missing .ttf references)
#
# Trade-offs accepted:
# - 404s for .ttf URLs in browser console (harmless - browser uses .woff2 successfully)
#
# See: specs/2025-10-13_no_cdn_FIXES.md for complete rationale
STORAGES["staticfiles"]["BACKEND"] = (  # noqa
    "config.storage.NonStrictCompressedStaticFilesStorage"
)


# TEMPLATE CONFIGURATION
# ------------------------------------------------------------------------------
# In production, we don't want to use the custom template loader
# since steeloweb is installed as a package

# Remove loaders if it exists to avoid conflict with APP_DIRS
if "loaders" in TEMPLATES[0]["OPTIONS"]:  # noqa
    del TEMPLATES[0]["OPTIONS"]["loaders"]  # noqa

# Make sure APP_DIRS is True to find templates in installed packages
TEMPLATES[0]["APP_DIRS"] = True  # noqa

# DATABASE CONFIGURATION
# ------------------------------------------------------------------------------

# Use SQLite in production with DATABASE_URL
# This allows for potential future DB changes without code modification
if env("DATABASE_URL", default=None):  # noqa
    DATABASES["default"] = env.db("DATABASE_URL")  # noqa
# Otherwise use the default SQLite configuration from base.py

# CACHING
# ------------------------------------------------------------------------------

# Caching
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": env("DJANGO_CACHE_LOCATION"),  # noqa
        "TIMEOUT": 600,
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}
CACHE_MIDDLEWARE_ALIAS = "default"
CACHE_MIDDLEWARE_SECONDS = 600
CACHE_MIDDLEWARE_KEY_PREFIX = "steeloweb"

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {
        "level": "WARNING",
        "handlers": [
            "console",
        ],
    },
    "formatters": {
        "verbose": {"format": "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s"},
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django.db.backends": {
            "level": "ERROR",
            "handlers": [
                "console",
            ],
            "propagate": False,
        },
        "django.security.DisallowedHost": {
            "level": "ERROR",
            "handlers": [
                "console",
            ],
            "propagate": False,
        },
        "cast": {
            "handlers": [
                "console",
            ],
            "propagate": True,
            "level": "DEBUG",
        },
        "indieweb": {
            "handlers": [
                "console",
            ],
            "propagate": True,
            "level": "DEBUG",
        },
    },
}
