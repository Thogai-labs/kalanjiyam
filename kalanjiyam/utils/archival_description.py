"""Reading and curating a project's archival description.

Three jobs, all of them glue between the extraction tables and the things that
consume them:

* **Read** -- merge the latest run's generated fields with the project's curated
  layer into one description a template can iterate over (`describe`).
* **Curate** -- write the curated layer (`set_curated`, `clear_curated`), which
  no extraction run ever touches.
* **Write down** -- seed the bibliographic columns the search index and the
  public book page read, from the archival tags (`write_down`).

The write-down exists because the two vocabularies overlap but only one of them
is indexed. `Project.print_title` and `.author` feed `search/indexer.py`, the
public book page and tenant search; TITLE and CREATOR are the same facts read
from the whole document with evidence attached. Extracting both separately would
mean two passes over one PDF and two answers to "what is this called".

It only ever fills a column that is **empty**. A moderator's typing outranks a
generation -- the same rule `tasks.metadata.apply_bibliographic` already enforces,
which is why this reuses it rather than reimplementing it.
"""

from __future__ import annotations

from datetime import datetime

from kalanjiyam import database as db
from kalanjiyam.utils import archival_taxonomy as at

__all__ = [
    "describe",
    "latest_run",
    "run_log",
    "set_curated",
    "clear_curated",
    "write_down",
    "TagView",
]

#: Statuses a run must reach before its fields are worth showing.
_USABLE = ("COMPLETED", "PARTIAL")


class TagView:
    """One tag, ready to render: value, provenance, evidence and origin.

    Deliberately not a model. A tag on the description tab is a merge of two rows
    that may or may not exist, plus taxonomy metadata that lives in neither, so
    there is no table this maps to.
    """

    def __init__(self, tag: at.Tag, generated=None, curated=None):
        self.tag = tag
        self.generated = generated
        self.curated = curated

    @property
    def code(self) -> str:
        return self.tag.code

    @property
    def is_locked(self) -> bool:
        """True for the three tags the extractor is never asked for."""
        return self.tag.code in at.WRITE_LOCKED

    @property
    def is_curated(self) -> bool:
        return self.curated is not None and not at.is_empty(self.curated.curated_value)

    @property
    def value(self):
        """Curation outranks a generated value."""
        if self.is_curated:
            return self.curated.curated_value
        return self.generated.value if self.generated is not None else None

    @property
    def is_empty(self) -> bool:
        return at.is_empty(self.value)

    @property
    def source(self) -> str | None:
        if self.is_curated:
            return at.SOURCE_CURATED
        return self.generated.source if self.generated is not None else None

    @property
    def confidence(self) -> float | None:
        """None on a curated value: a human's entry is not scored, not perfect."""
        if self.is_curated or self.generated is None:
            return None
        return self.generated.confidence

    @property
    def evidence(self) -> list:
        if self.is_curated or self.generated is None:
            return []
        return list(self.generated.evidence)

    @property
    def superseded(self):
        """The generated value a curated one is standing in front of, if any.

        Shown so an archivist can see what the machine said before overriding it,
        rather than the override silently hiding it.
        """
        if not self.is_curated or self.generated is None:
            return None
        return self.generated.value if not at.is_empty(self.generated.value) else None

    def evidence_for(self, index: int) -> list:
        """Spans citing one entry of a list-valued tag."""
        return [e for e in self.evidence if e.value_index == index]

    @property
    def unindexed_evidence(self) -> list:
        """Spans for a single-valued tag, which carry no index."""
        return [e for e in self.evidence if e.value_index is None]


def latest_run(session, project_id: int):
    """The most recent run worth showing, or None."""
    return (
        session.query(db.MetadataExtractionRun)
        .filter(
            db.MetadataExtractionRun.project_id == project_id,
            db.MetadataExtractionRun.status.in_(_USABLE),
        )
        .order_by(db.MetadataExtractionRun.id.desc())
        .first()
    )


def last_attempt(session, project_id: int):
    """The most recent run of any status, including a failed one.

    Separate from `latest_run` so a failure is reported rather than silently
    leaving the tab looking as though nothing was ever tried.
    """
    return (
        session.query(db.MetadataExtractionRun)
        .filter(db.MetadataExtractionRun.project_id == project_id)
        .order_by(db.MetadataExtractionRun.id.desc())
        .first()
    )


def run_log(session, project_id: int, run_id: int | None = None) -> dict:
    """Every run of a project, and the window-by-window record of one of them.

    The description tab shows the newest usable run and nothing else, which is
    right for reading a catalogue record and useless for answering "why did this
    stop at eight pages of ten". That answer is per window: which pages it
    covered, whether the call succeeded, what the service said when it did not.

    Defaults to the newest *attempt* rather than the newest usable run -- a run
    that failed outright is exactly the one someone opening a log wants.
    """
    runs = (
        session.query(db.MetadataExtractionRun)
        .filter(db.MetadataExtractionRun.project_id == project_id)
        .order_by(db.MetadataExtractionRun.id.desc())
        .all()
    )

    selected = None
    if run_id is not None:
        selected = next((r for r in runs if r.id == run_id), None)
    elif runs:
        selected = runs[0]

    windows = []
    if selected is not None:
        windows = (
            session.query(db.MetadataWindow)
            .filter(db.MetadataWindow.run_id == selected.id)
            .order_by(db.MetadataWindow.window_index)
            .all()
        )

    return {
        "runs": runs,
        "run": selected,
        "windows": windows,
        "failed_windows": [w for w in windows if w.status == "FAILED"],
    }


def _curated_rows(session, project_id: int) -> dict:
    rows = (
        session.query(db.MetadataField)
        .filter(
            db.MetadataField.project_id == project_id,
            db.MetadataField.run_id.is_(None),
        )
        .all()
    )
    return {row.tag_code: row for row in rows}


def _generated_rows(session, run) -> dict:
    if run is None:
        return {}
    rows = (
        session.query(db.MetadataField).filter(db.MetadataField.run_id == run.id).all()
    )
    return {row.tag_code: row for row in rows}


def describe(session, project_id: int) -> dict:
    """The whole description, grouped for display.

    Returns the taxonomy's own grouping with a `TagView` per tag, so a tag with
    no value still renders as an empty slot -- an archival description is judged
    as much by what is missing as by what is filled.
    """
    run = latest_run(session, project_id)
    curated = _curated_rows(session, project_id)
    generated = _generated_rows(session, run)

    groups = []
    views = {}
    for name, tags in at.GROUPS:
        group = []
        for tag in tags:
            view = TagView(tag, generated.get(tag.code), curated.get(tag.code))
            views[tag.code] = view
            group.append(view)
        groups.append((name, group))

    filled = [v for v in views.values() if not v.is_empty]
    return {
        "run": run,
        "attempt": last_attempt(session, project_id),
        "groups": groups,
        "views": views,
        "filled": len(filled),
        "total": len(views),
        "curated_count": sum(1 for v in views.values() if v.is_curated),
    }


def set_curated(session, project_id: int, tag_code: str, value, user_id=None):
    """Record an archivist's value for one tag.

    An empty value clears the curation rather than storing a blank, so that
    emptying the box restores whatever the extractor found instead of masking it
    with an empty string.
    """
    if tag_code not in at.BY_CODE:
        raise ValueError(f"unknown tag {tag_code!r}")

    if at.is_empty(value):
        clear_curated(session, project_id, tag_code)
        return None

    row = (
        session.query(db.MetadataField)
        .filter(
            db.MetadataField.project_id == project_id,
            db.MetadataField.run_id.is_(None),
            db.MetadataField.tag_code == tag_code,
        )
        .first()
    )
    if row is None:
        row = db.MetadataField(project_id=project_id, tag_code=tag_code)
        session.add(row)
        # The app's sessions are autoflush=False, so without this the next
        # lookup would miss the pending row and insert a second one.
        session.flush()

    row.curated_value = value
    row.is_curated = True
    row.source = at.SOURCE_CURATED
    row.curated_by_id = user_id
    row.curated_at = datetime.utcnow()
    return row


def clear_curated(session, project_id: int, tag_code: str) -> bool:
    """Drop the curated row for one tag. True if there was one."""
    row = (
        session.query(db.MetadataField)
        .filter(
            db.MetadataField.project_id == project_id,
            db.MetadataField.run_id.is_(None),
            db.MetadataField.tag_code == tag_code,
        )
        .first()
    )
    if row is None:
        return False
    session.delete(row)
    return True


# Write-down to the bibliographic columns
# ---------------------------------------

#: Archival tag -> the key `tasks.metadata.apply_bibliographic` expects. Only
#: tags with an honest single-string reading appear here; PERSON NAME has no
#: column because "every person named anywhere in the file" is not an author.
_COLUMN_TAGS = {
    "TITLE": "title",
    "DATE": "year",
    "CREATOR": "author",
}


def _first_label(value) -> str:
    """The highest-ranked entity label from a list-valued tag.

    `archival_reduce.merge_entities` ranks by mention frequency, so the first
    entry is the one the document actually leans on.
    """
    for item in value or []:
        if isinstance(item, dict) and (item.get("label") or "").strip():
            return item["label"].strip()
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _labels(value) -> list[str]:
    out = []
    for item in value or []:
        if isinstance(item, dict) and (item.get("label") or "").strip():
            out.append(item["label"].strip())
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _scalar(fields: dict, code: str) -> str:
    blob = fields.get(code) or {}
    value = blob.get("value")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return _first_label(value)
    return ""


def _languages(fields: dict) -> list[dict]:
    """LANGUAGE entities in the shape `extracted_metadata["languages"]` uses."""
    blob = fields.get("LANGUAGE") or {}
    out = []
    for item in blob.get("value") or []:
        if not isinstance(item, dict):
            continue
        code = (item.get("auth_id") or item.get("label") or "").strip()
        if not code:
            continue
        entry = {"code": code}
        if item.get("note"):
            entry["role"] = str(item["note"]).strip()
        out.append(entry)
    return out


def write_down(session, project, fields: dict) -> dict:
    """Seed the search-facing columns from a finished description.

    Returns `apply_bibliographic`'s applied/skipped report, extended with what it
    did to `extracted_metadata`. Nothing here overwrites an existing value.
    """
    # Imported here rather than at module scope: `tasks.metadata` pulls in Celery
    # and the LLM client, and this module is read by the web request that renders
    # the tab.
    from kalanjiyam.tasks import metadata as metadata_tasks

    data = {}
    for code, key in _COLUMN_TAGS.items():
        value = _scalar(fields, code)
        if value:
            data[key] = value

    formats = _labels((fields.get("DOCUMENT FORMAT") or {}).get("value"))
    if formats:
        data["genre"] = formats[0]

    report = metadata_tasks.apply_bibliographic(session, project, data)

    existing = project.extracted_metadata or {}
    content = dict(existing.get("content") or {})
    merged = dict(existing)
    changed = []

    summary = _scalar(fields, "SCOPE CONTENT")
    if summary and not (content.get("summary") or "").strip():
        content["summary"] = summary
        changed.append("summary")

    keywords = _labels((fields.get("SUBJECT") or {}).get("value"))
    if keywords and not content.get("keywords"):
        content["keywords"] = keywords
        changed.append("keywords")

    languages = _languages(fields)
    if languages and not merged.get("languages"):
        merged["languages"] = languages
        changed.append("languages")

    if changed:
        merged["content"] = content
        project.extracted_metadata = merged
        # SQLAlchemy does not track in-place mutation of a JSON column.
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(project, "extracted_metadata")

    report["metadata_applied"] = changed
    return report
