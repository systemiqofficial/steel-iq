import logging
import os
import signal

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SteelowebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "steeloweb"

    def ready(self):
        # Only register shutdown handler in standalone mode
        if os.getenv("STEELO_STANDALONE") == "1":
            # Add shutdown handler for worker cleanup
            def shutdown_handler(signum, frame):
                logger.info("Received shutdown signal, draining all workers...")
                from steeloweb.models import Worker

                # Mark all active workers as DRAINING
                updated = Worker.objects.filter(state__in=["STARTING", "RUNNING"]).update(state="DRAINING")

                logger.info(f"Marked {updated} workers as DRAINING")

            signal.signal(signal.SIGTERM, shutdown_handler)
            signal.signal(signal.SIGINT, shutdown_handler)
            logger.info("Registered shutdown handler for worker cleanup")
