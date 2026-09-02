"""
Foundry — background jobs for the panel.

Why this exists, precisely: `Build all` took **62 seconds** on eight demo sites
and one of them (26,422 pages) was 47 of those seconds on its own. Flask
answered when it finished, so the browser sat on a blank pending request the
whole time — no spinner, no progress, nothing moved. Clicking anything else in
that tab then looked broken too, because the tab was still waiting on the first
navigation.

The button worked. It just looked exactly like a button that does not.

So builds run on a worker thread and the page redirects immediately to a status
view that refreshes. Two rules keep it honest:

  ONE BUILD AT A TIME.  Builds write into `dist/` and into `data/shipped.json`.
  Two at once would interleave those writes and the similarity record would
  depend on who finished first. A second request joins the running job rather
  than starting a rival one.

  RESULTS ARE SLIM.  A finished build holds every rendered page in memory —
  26,000 pages at ~12 KB is ~300 MB. The job keeps counts and findings, never
  the HTML. The pages are already on disk, which is the point of the build.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


# A build should never take longer than this. Past it, the job is treated as
# wedged and reaped so the next build can start — without this, one hung build
# leaves the panel "stuck" forever, because every later build joins the dead one
# instead of starting. Generous by design; a real build finishes far sooner.
MAX_JOB_SECONDS = 1800


@dataclass
class Job:
    id: str
    targets: list[str]
    kind: str = "build"            # "build" or "deploy" — picks the status view
    done: list[dict] = field(default_factory=list)
    current: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    thread: Any = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    @property
    def progress(self) -> int:
        return round(100 * len(self.done) / max(1, len(self.targets)))

    @property
    def passed(self) -> int:
        return sum(1 for d in self.done if d.get("ok"))


class Runner:
    """One job at a time, kept in memory. Restarting the panel forgets them,
    which is correct — the artefacts live in dist/, not here."""

    def __init__(self, keep: int = 12):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._active: str | None = None
        self._keep = keep

    def _reap(self, job: Job | None) -> None:
        """Finalize a job that is wedged: its worker thread has died without
        marking it finished (should not happen, but defends against it), or it
        has run past MAX_JOB_SECONDS. Either way, stop treating it as running so
        the next build is not blocked behind a corpse. Caller holds the lock."""
        if not job or job.finished is not None:
            return
        dead = job.thread is not None and not job.thread.is_alive()
        stale = (time.time() - job.started) > MAX_JOB_SECONDS
        if dead or stale:
            job.current = None
            job.finished = time.time()
            if not job.error:
                job.error = ("The build worker stopped unexpectedly."
                             if dead else
                             f"The build exceeded {MAX_JOB_SECONDS // 60} minutes "
                             "and was released so new builds can run.")

    def active(self) -> Job | None:
        with self._lock:
            job = self._jobs.get(self._active) if self._active else None
            self._reap(job)
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            self._reap(job)
            return job

    def recent(self) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order) if i in self._jobs]

    def start(self, targets: list[str], work: Callable[[str], dict[str, Any]],
              kind: str = "build") -> Job:
        """One job at a time across BOTH kinds. A build rewrites dist/ from
        scratch; a deploy reads it. Letting them overlap would upload a
        directory that is being deleted underneath the upload."""
        with self._lock:
            running = self._jobs.get(self._active) if self._active else None
            self._reap(running)                # release a wedged job first
            if running and running.running:
                # Join the running job rather than racing it into the same dist/.
                return running

            job_id = f"{kind[0]}{int(time.time() * 1000) % 10_000_000}"
            job = Job(id=job_id, targets=list(targets), kind=kind)
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._active = job_id
            for old in self._order[:-self._keep]:
                self._jobs.pop(old, None)
            self._order = self._order[-self._keep:]

        def run() -> None:
            try:
                for target in job.targets:
                    job.current = target
                    job.done.append(work(target))
            except Exception:                                  # noqa: BLE001
                job.error = traceback.format_exc()[-1500:]
            finally:
                job.current = None
                job.finished = time.time()

        job.thread = threading.Thread(target=run, daemon=True, name=f"build-{job_id}")
        job.thread.start()
        return job


RUNNER = Runner()
