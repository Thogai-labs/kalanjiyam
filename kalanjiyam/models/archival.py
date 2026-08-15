"""Archival description extraction: runs, windows, fields and evidence.

Four tables, in the shape the OCR pipeline already proved:

* `MetadataExtractionRun` -- one per project per extraction, holding the rollups.
  The `BatchItem` analogue.
* `MetadataWindow` -- one per model call, holding that call's own metrics so a
  failed window can be retried alone. The `BatchOcrPage` analogue.
* `MetadataField` -- one per tag after the reduce. The description itself.
* `MetadataEvidence` -- one per citation span.

Two design rules run through all of it.

**Machine output and curated values never share a column.** `MetadataField` keeps
`value` (whatever the last run produced) separate from `curated_value` (what an
archivist typed). A re-run rewrites the former and must never touch the latter,
which is the same invariant `tasks.metadata._save` enforces for the JSON blob.

**Confidence is nullable everywhere, and nulls stay null.** Three of the OCR
engines in service are VLM-based and produce no confidence signal at all, so a
page can legitimately have none. Coercing that to 0.0 would quarantine every page
they touch; coercing it to 1.0 would assert quality nothing measured. Averages are
taken over the rows that have a score, and the count that did not is recorded
beside them -- see `pages_without_confidence`.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from kalanjiyam.models.base import Base, pk

__all__ = [
    "MetadataExtractionRun",
    "MetadataWindow",
    "MetadataField",
    "MetadataEvidence",
]

#: Field confidences below this are surfaced for archivist review. Mirrors the
#: 0.7 floor the OCR metrics use for `low_conf_page_count`.
LOW_CONFIDENCE = 0.7


class MetadataExtractionRun(Base):
    """One extraction pass over one project, with its aggregate metrics."""

    __tablename__ = "metadata_extraction_runs"

    id = pk()
    project_id = Column(
        Integer,
        ForeignKey("proof_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: PENDING, IN_PROGRESS, COMPLETED, PARTIAL, FAILED.
    status = Column(String(64), nullable=False, default="PENDING")

    # Provenance. A run that cannot say which schema and model produced it is
    # not usable as evidence, so these are recorded even on failure.
    engine = Column(String(64), nullable=True)
    model_name = Column(String(128), nullable=True)
    model_version = Column(String(64), nullable=True)
    taxonomy_version = Column(String(64), nullable=True)
    contract_version = Column(String(16), nullable=True)

    # Coverage.
    windows_total = Column(Integer, nullable=True)
    windows_completed = Column(Integer, nullable=True)
    windows_failed = Column(Integer, nullable=True)
    pages_total = Column(Integer, nullable=True)
    #: Distinct pages actually sent. With `pages_total`, this is the number that
    #: says whether a full pass ran or a sample did.
    pages_read = Column(Integer, nullable=True)
    fields_filled = Column(Integer, nullable=True)
    fields_total = Column(Integer, nullable=True)

    # Quality.
    avg_field_confidence = Column(Float, nullable=True)
    min_field_confidence = Column(Float, nullable=True)
    low_conf_field_count = Column(Integer, nullable=True)
    evidence_spans = Column(Integer, nullable=True)
    evidence_verified = Column(Integer, nullable=True)
    #: Verified spans over spans that *could* be verified (i.e. `record` values).
    evidence_verified_rate = Column(Float, nullable=True)
    #: Mean OCR confidence of the pages read, over pages that carry one. NULL
    #: when none of them did.
    avg_source_ocr_confidence = Column(Float, nullable=True)
    #: How many pages read had no OCR confidence signal at all. Reported beside
    #: the average so the average is never mistaken for whole-document coverage.
    pages_without_confidence = Column(Integer, nullable=True)

    # Cost.
    total_prompt_tokens = Column(Integer, nullable=True)
    total_completion_tokens = Column(Integer, nullable=True)
    total_engine_latency_ms = Column(Float, nullable=True)
    total_extraction_latency_ms = Column(Float, nullable=True)
    metadata_data_size_bytes = Column(Integer, nullable=True)

    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project")
    windows = relationship(
        "MetadataWindow", back_populates="run", cascade="all, delete-orphan"
    )
    fields = relationship(
        "MetadataField", back_populates="run", cascade="all, delete-orphan"
    )

    @property
    def extraction_coverage(self) -> float | None:
        """Pages read over pages available. None when the run recorded neither."""
        if not self.pages_total:
            return None
        return (self.pages_read or 0) / self.pages_total

    @property
    def field_coverage(self) -> float | None:
        """Tags filled over tags in the schema."""
        if not self.fields_total:
            return None
        return (self.fields_filled or 0) / self.fields_total

    @property
    def total_tokens(self) -> int | None:
        """Input plus output over the whole document. None if neither was reported."""
        if self.total_prompt_tokens is None and self.total_completion_tokens is None:
            return None
        return (self.total_prompt_tokens or 0) + (self.total_completion_tokens or 0)

    @property
    def tokens_per_page(self) -> float | None:
        """Tokens over pages read -- an *average*, not a measurement.

        Tokens are spent per window, not per page, and the two do not divide
        cleanly: windows overlap by one page, so a page on a boundary is billed
        twice, and the taxonomy instruction (~3k tokens) is charged once per
        window however many pages that window happens to hold. A short window at
        the end of a document therefore costs far more per page than a full one.

        Useful for forecasting the cost of a comparable document. Not usable as
        a per-page bill, which is why the per-window figures are kept alongside
        rather than being replaced by this.
        """
        if not self.pages_read:
            return None
        total = self.total_tokens
        return (total / self.pages_read) if total is not None else None

    @property
    def tokens_per_window(self) -> float | None:
        """Tokens over windows completed -- what is actually charged per call."""
        if not self.windows_completed:
            return None
        total = self.total_tokens
        return (total / self.windows_completed) if total is not None else None


class MetadataWindow(Base):
    """One model call over one window of pages."""

    __tablename__ = "metadata_windows"
    __table_args__ = (
        UniqueConstraint("run_id", "window_index", name="uq_metadata_window_index"),
    )

    id = pk()
    run_id = Column(
        Integer,
        ForeignKey("metadata_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: 1-based, matching the `window.index` sent in the request.
    window_index = Column(Integer, nullable=False)

    #: PENDING, IN_PROGRESS, COMPLETED, FAILED, SKIPPED.
    status = Column(String(64), nullable=False, default="PENDING")
    attempt_count = Column(Integer, nullable=False, default=0)

    #: Page slugs in this window, in order. JSON rather than a join table: the
    #: list is read whole and never queried by element.
    page_slugs = Column(JSON, nullable=True)
    #: SHA-256 of the window's text. A re-run skips windows whose hash is
    #: unchanged, which is what makes re-extraction after a proofreading fix
    #: affordable.
    text_hash = Column(String(64), nullable=True, index=True)

    fields_attempted = Column(Integer, nullable=True)
    fields_returned = Column(Integer, nullable=True)
    fields_declined = Column(Integer, nullable=True)

    chars_in = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    engine_latency_ms = Column(Float, nullable=True)
    #: Wall time around the call. Minus `engine_latency_ms` gives queue and
    #: transport overhead, the same split the OCR metrics make.
    extraction_latency_ms = Column(Float, nullable=True)

    avg_field_confidence = Column(Float, nullable=True)
    min_field_confidence = Column(Float, nullable=True)
    low_conf_field_count = Column(Integer, nullable=True)
    evidence_spans = Column(Integer, nullable=True)
    evidence_verified = Column(Integer, nullable=True)
    #: NULL when no page in the window carried an OCR confidence score.
    source_ocr_confidence = Column(Float, nullable=True)
    pages_without_confidence = Column(Integer, nullable=True)

    #: The raw response, kept for debugging a bad window without re-running it.
    raw_response = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    run = relationship("MetadataExtractionRun", back_populates="windows")


class MetadataField(Base):
    """One tag of the finished description.

    Rows come in two kinds, distinguished by `run_id`:

    * **Generated** (`run_id` set) -- what a run produced. Replaced wholesale by
      the next run, and deleted with it.
    * **Curated** (`run_id` NULL) -- what an archivist typed, keyed by project and
      tag. A run never reads or writes these, which is what makes "a regenerate
      must not destroy curation" true by construction rather than by discipline.

    The curated row also has to exist *before* any run does: the three write-locked
    tags are never extracted at all, so requiring a run to hang them from would
    make them unenterable on a project that has not been extracted yet.
    """

    __tablename__ = "metadata_fields"
    __table_args__ = (
        UniqueConstraint("run_id", "tag_code", name="uq_metadata_field_tag"),
    )

    id = pk()
    #: NULL on a curated row -- see the class docstring. Uniqueness for those is
    #: enforced by a partial index on (project_id, tag_code), because a NULL
    #: never collides in a UNIQUE constraint.
    run_id = Column(
        Integer,
        ForeignKey("metadata_extraction_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: Denormalised so the current description for a project can be read without
    #: joining through the run.
    project_id = Column(
        Integer,
        ForeignKey("proof_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: A code from `utils.archival_taxonomy.BY_CODE`, e.g. "PERSON NAME".
    tag_code = Column(String(64), nullable=False, index=True)

    #: What the reduce produced: a string for text/prose tags, a list of entity
    #: or relation objects otherwise.
    value = Column(JSON, nullable=True)
    #: What an archivist entered. Never written by an extraction run. The three
    #: write-locked tags only ever have this.
    curated_value = Column(JSON, nullable=True)
    is_curated = Column(Boolean, nullable=False, default=False)

    confidence = Column(Float, nullable=True)
    #: One of `archival_taxonomy.SOURCES`.
    source = Column(String(32), nullable=True)

    curated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    curated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("MetadataExtractionRun", back_populates="fields")
    evidence = relationship(
        "MetadataEvidence", back_populates="field", cascade="all, delete-orphan"
    )

    @property
    def effective_value(self):
        """What the catalogue should show: curation outranks a generated value."""
        return self.curated_value if self.is_curated else self.value

    @property
    def is_curated_row(self) -> bool:
        """True for the project-scoped curation layer, false for run output."""
        return self.run_id is None


class MetadataEvidence(Base):
    """One citation span backing a value.

    Its own table rather than a JSON blob because it is both the join to the page
    image (via `block_id`, whose bbox lives in `Revision.document`) and the basis
    of every quality metric. Buried in JSON, neither is queryable.
    """

    __tablename__ = "metadata_evidence"

    id = pk()
    field_id = Column(
        Integer,
        ForeignKey("metadata_fields.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Index into a list-valued field, so a citation points at one name among
    #: forty rather than at the list. NULL for single-valued tags.
    value_index = Column(Integer, nullable=True)

    page_slug = Column(String(255), nullable=True, index=True)
    #: Block id from the OCR response. NULL is tolerated -- the quote can still
    #: be verified against the page, it just loses the link to the image region.
    block_id = Column(String(64), nullable=True)
    #: The span as the model quoted it. NULL for `derived` values, which cite a
    #: page range rather than text.
    quote = Column(Text, nullable=True)

    #: True when `quote` was found in the page text actually sent. False means
    #: the model produced a quote that is not in the source -- i.e. invented it.
    #: NULL when verification does not apply (`derived`, `enrichment`, `curated`).
    verified = Column(Boolean, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    field = relationship("MetadataField", back_populates="evidence")
