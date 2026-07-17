"""Observability - logging setup in one place.

Two rules the rest of the app follows:
  - configure_logging() is called ONCE, at an entrypoint (main.py on startup,
    evals/run.py before a run). Libraries never configure logging; apps do.
  - every module gets its own logger with `log = logging.getLogger(__name__)`,
    so a log line tells you which module emitted it.

Level comes from the LOG_LEVEL env var (default INFO). Set LOG_LEVEL=DEBUG to
see per-fact reconcile decisions; INFO shows per-report / per-request summaries.
"""

import logging
import os

_CONFIGURED = False


def configure_logging() -> None:
    """Idempotent: safe to call from more than one entrypoint; a second call
    is a no-op."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        # asctime | level | which module | message. A real service would emit
        # JSON here (structured logs) and ship to Sentry/a log store; plain
        # text keeps the demo readable in a terminal.
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    _CONFIGURED = True

    # Confirm LLM tracing state so it's obvious in the logs whether traces are
    # being shipped. LangChain/LangGraph read LANGSMITH_TRACING itself - we only
    # report it here; enabling it is env-only, no code change (see .env.example).
    if os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes"):
        logging.getLogger(__name__).info(
            "LangSmith tracing ENABLED (project=%s)",
            os.getenv("LANGSMITH_PROJECT", "default"),
        )
