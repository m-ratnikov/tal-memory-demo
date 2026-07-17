"""Tiny in-process background jobs - just enough for the migration wizard.

A distill run takes minutes (one extract + one judge call per source); an HTTP
handler cannot block that long. This runs the work on a daemon thread and lets
the wizard page poll the status. DELIBERATELY minimal: one job per name, state
in a module dict, lost on restart. The production answer is a real task queue
(Celery/arq) with persistence and retries - named in ARCHITECTURE; the demo's
pipelines are already resumable via their ledgers, which is what makes this
simple runner safe: a killed distill just resumes on the next click.

Threads are fine here - the work is I/O-bound (API calls).
"""

import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def start(name: str, fn: Callable[[], Any]) -> bool:
    """Start fn on a background thread under this job name. Returns False if
    a run with this name is already in flight (no double-starts)."""
    with _lock:
        if _jobs.get(name, {}).get("status") == "running":
            return False
        _jobs[name] = {"status": "running", "result": None, "error": None}

    def _run() -> None:
        try:
            result = fn()
            _jobs[name].update(status="done", result=result)
            log.info("job %s: done", name)
        except Exception as exc:  # surface, never swallow silently
            _jobs[name].update(status="error", error=str(exc))
            log.exception("job %s: failed", name)

    threading.Thread(target=_run, name=f"job-{name}", daemon=True).start()
    log.info("job %s: started", name)
    return True


def status(name: str) -> dict:
    """Current state: {'status': idle|running|done|error, 'result', 'error'}."""
    return _jobs.get(name, {"status": "idle", "result": None, "error": None})


def clear(name: str) -> None:
    """Forget a job's state - used by the wizard's reset so the page returns to
    its pristine 'nothing has run yet' look."""
    with _lock:
        _jobs.pop(name, None)
