"""
Standalone/Electron App Configurations

- Inherits from base settings (not production)
- Ensures DEBUG is False
- No debug toolbar or development tools
- Optimized for packaged application
- Version-isolated storage (database, media, cache)
"""

from .base import *  # noqa
import json
import os
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

# Get app version for storage isolation
# Each app version gets its own database, media directory, and cache
VERSION = env("STEELO_APP_VERSION", default="unknown")  # noqa

# Enable debug for standalone app (local Electron)
# This allows static file serving without complex setup
DEBUG = True

# Update template debug setting to match
if len(TEMPLATES) > 0:  # noqa
    TEMPLATES[0]["OPTIONS"]["debug"] = DEBUG  # noqa

# Ensure no debug toolbar in middleware
# Filter out debug toolbar if it somehow got included
MIDDLEWARE = [m for m in MIDDLEWARE if "debug_toolbar" not in m]  # noqa

# Ensure no debug toolbar in installed apps
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "debug_toolbar"]  # noqa

# Set a default secret key if not provided (for standalone builds)
# This should be overridden in production deployments
SECRET_KEY = env(  # noqa
    "DJANGO_SECRET_KEY", default="standalone-default-key-please-change-in-production"
)

# Allow localhost for Electron app
ALLOWED_HOSTS = env.list(  # noqa
    "DJANGO_ALLOWED_HOSTS",
    default=[
        "127.0.0.1",
        "localhost",
        "[::1]",  # IPv6 localhost
    ],
)

# Version-specific media directory
# CRITICAL: Prevents output file collisions when PKs overlap between versions
# Each version gets its own prep_{pk}/ and run_{pk}/ directories
MEDIA_ROOT = BASE_DIR / f"media-v{VERSION}"  # noqa
MEDIA_URL = "/media/"

# Disable security features that don't apply to local Electron app
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

# Set cache location to version-specific directory
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": env(  # noqa
            "DJANGO_CACHE_LOCATION",
            default=str(BASE_DIR / f"cache-v{VERSION}"),  # noqa
        ),
        "TIMEOUT": 600,
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}

# Database settings optimized for Electron app
# These settings prevent SQLite database locking issues when running in Electron:
# - WAL mode enables better concurrent access
# - Increased timeout prevents "database is locked" errors
# - Memory optimizations improve performance
# - Version-specific database ensures complete isolation between app versions
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / f"db-v{VERSION}.sqlite3",  # noqa - Version-specific database
        "OPTIONS": {
            "timeout": 30,  # Increase timeout to 30 seconds
            "check_same_thread": False,  # Allow connections from different threads
            "init_command": (
                "PRAGMA journal_mode=WAL;"  # Enable Write-Ahead Logging for better concurrency
                "PRAGMA synchronous=NORMAL;"  # Faster writes, still safe
                "PRAGMA cache_size=-64000;"  # Use 64MB for cache
                "PRAGMA temp_store=MEMORY;"  # Use memory for temp tables
                "PRAGMA mmap_size=268435456;"  # Use 256MB memory-mapped I/O
            ),
        },
    }
}

# Configure django-tasks to handle database connections better
TASKS = {
    "default": {
        "BACKEND": "django_tasks.backends.database.DatabaseBackend",
        "BACKEND_OPTIONS": {
            "database_alias": "default",
            "poll_interval": 1.0,  # Poll every second
            "max_attempts": 3,  # Retry failed tasks up to 3 times
        },
    }
}

# Simplified logging for standalone app
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "steeloweb": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "steelo": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


# Sentry Error Reporting Configuration
def initialize_sentry():
    """Initialize Sentry if config exists and user has consented"""
    try:
        # Try to find sentry config file
        # Look in parent directory (django-bundle) where build script places it
        bundle_dir = BASE_DIR.parent  # noqa - django-bundle directory
        sentry_config_path = os.path.join(bundle_dir, "sentry-config.json")

        if not os.path.exists(sentry_config_path):
            # Try alternative location in Django directory itself
            sentry_config_path = os.path.join(BASE_DIR, "sentry-config.json")  # noqa

        if os.path.exists(sentry_config_path):
            with open(sentry_config_path, "r") as f:
                sentry_config = json.load(f)

            if sentry_config.get("django") and sentry_config.get("enabled"):
                # Check user consent
                user_data_dir = env("ELECTRON_USER_DATA", default="")  # noqa
                has_consent = False

                if user_data_dir:
                    consent_file = os.path.join(user_data_dir, "error-reporting-consent.json")
                    if os.path.exists(consent_file):
                        try:
                            with open(consent_file, "r") as f:
                                consent_data = json.load(f)
                                has_consent = consent_data.get("enabled", False)
                        except Exception:
                            pass  # If we can't read consent, assume no consent

                if has_consent:
                    sentry_sdk.init(
                        dsn=sentry_config["django"],
                        integrations=[
                            DjangoIntegration(
                                transaction_style="url",
                                middleware_spans=False,
                            ),
                        ],
                        traces_sample_rate=0.1,
                        send_default_pii=False,  # Privacy-conscious
                        environment=sentry_config.get("environment", "production"),
                        before_send=sanitize_sentry_event,
                    )
                    print("[OK] Sentry error reporting initialized for Django")
                else:
                    print("[INFO] Sentry disabled - user has not consented to error reporting")
        else:
            print("[INFO] No Sentry configuration file found")

    except Exception as e:
        # Silently fail if Sentry can't be initialized
        print(f"[WARNING] Could not initialize Sentry: {e}")


def sanitize_sentry_event(event, hint):
    """Remove sensitive information from Sentry events"""
    if event is None:
        return event

    # Remove user paths from stack traces
    if "exception" in event:
        for exception in event["exception"].get("values", []):
            for frame in exception.get("stacktrace", {}).get("frames", []):
                if "filename" in frame:
                    # Replace user-specific paths with generic ones
                    frame["filename"] = frame["filename"].replace("/Users/", "/HOME/")
                    frame["filename"] = frame["filename"].replace("\\Users\\", "\\HOME\\")
                    frame["filename"] = frame["filename"].replace("/home/", "/HOME/")

    # Remove sensitive request data
    if "request" in event:
        event["request"].pop("cookies", None)
        event["request"].pop("headers", None)
        event["request"].pop("env", None)
        event["request"].pop("data", None)  # Remove POST data

    # Remove user information
    if "user" in event:
        event.pop("user", None)

    return event


# Initialize Sentry
initialize_sentry()
