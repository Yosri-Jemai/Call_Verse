import logging
import uvicorn

from src.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy third-party loggers
for name in ("httpx", "httpcore", "urllib3", "hf_transfer", "uvicorn",
              "openai", "anthropic", "google", "google.auth", "google.generativeai",
              "matplotlib", "psycopg", "psycopg.pool"):
    logging.getLogger(name).setLevel(logging.WARNING)

logger = logging.getLogger("cashy.main")


def main():
    provider = settings.resolved_provider
    model_name = settings.model_name or "default"
    logger.info("Mode:     %s", settings.app_mode)
    if provider:
        logger.info("LLM:      %s (%s)", provider, model_name)
    else:
        logger.warning("LLM:      No API key configured — set a provider key in .env")
    logger.info("Database: %s@%s:%s", settings.resolved_db_name, settings.db_host[:8] + "...", settings.db_port)
    logger.info("Launching agent API at http://%s:%d", settings.api_host, settings.api_port)
    uvicorn.run(
        "src.api:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
