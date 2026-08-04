"""Model for search index maintenance jobs.

Rebuilds run in the background and can take a long time, so the job row --
not Celery's result backend -- is the source of truth for progress. That is
the same choice the batch OCR pipeline makes (see
:mod:`kalanjiyam.models.batch`), and it means the admin dashboard keeps
working across worker restarts.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from kalanjiyam.models.base import Base, pk

__all__ = ["SearchIndexJob"]

#: What the job does.
JOB_REBUILD = "REBUILD"
JOB_SYNC = "SYNC"
JOB_DROP = "DROP"
JOB_INDEX_PROJECT = "INDEX_PROJECT"

#: How wide the job reaches.
SCOPE_ALL = "ALL"
SCOPE_ORG = "ORG"
SCOPE_PROJECT = "PROJECT"

#: Lifecycle.
STATUS_PENDING = "PENDING"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)

#: Written into ``error_message`` so a running task can notice it was asked
#: to stop, the same cooperative-cancellation trick the batch pipeline uses.
CANCEL_MESSAGE = "Cancelled by user"


class SearchIndexJob(Base):
    """One search index maintenance run."""

    __tablename__ = "search_index_jobs"

    id = pk()

    #: REBUILD, SYNC, DROP, or INDEX_PROJECT.
    job_type = Column(String(32), nullable=False)
    #: ALL, ORG, or PROJECT.
    scope_kind = Column(String(16), nullable=False, default=SCOPE_ALL)
    #: The organization this job targets, when ``scope_kind`` is ORG.
    scope_org_id = Column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The project this job targets, when ``scope_kind`` is PROJECT.
    scope_project_id = Column(
        Integer,
        ForeignKey("proof_projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(String(32), nullable=False, default=STATUS_PENDING, index=True)

    #: Progress counters, updated as the job streams through documents.
    total_docs = Column(Integer, nullable=False, default=0)
    processed_docs = Column(Integer, nullable=False, default=0)
    failed_docs = Column(Integer, nullable=False, default=0)

    #: Who asked for it. Nullable because the CLI has no logged-in user.
    requested_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: For correlating with worker logs.
    celery_task_id = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def percent(self) -> float:
        if not self.total_docs:
            return 100.0 if self.is_terminal else 0.0
        return min(100.0, self.processed_docs / self.total_docs * 100)

    def __str__(self):
        return f"SearchIndexJob({self.id}, {self.job_type}, {self.status})"
