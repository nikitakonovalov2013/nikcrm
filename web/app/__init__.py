__all__ = []

# Initialize persistent logging for web service on import
try:
    from shared.logging import setup_logging
    from shared.config import settings

    setup_logging(service_name="web", log_dir="/var/log/app/web", level=settings.LOG_LEVEL)
except Exception:
    # Fallback: avoid crashing on import due to logging
    pass
