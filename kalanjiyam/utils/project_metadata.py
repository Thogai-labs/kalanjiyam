"""Deterministic project metadata.

Everything here is computed from the database alone -- no LLM calls. The
extraction task in `kalanjiyam.tasks.metadata` uses this module to decide
*which* text to send to the model and to fill in the facts the model must not
be asked to guess (page counts, script mix, OCR engines used).

The two pieces worth understanding:

1. **Track resolution.** Page text lives on version tracks (`PageVersion.
   version_key`): ``user:<id>``, ``role:p1``, ``role:p2``, ``role:moderator``,
   ``translation:*``, ``ocr:<engine>``. For metadata extraction we want the
   best *edited* text available and fall back to raw OCR when no human has
   touched the page. This deliberately does not reuse
   `views.proofing.page.resolve_version_keys`, which is user-relative and
   orders by recency -- extraction must be reproducible and quality-ordered.

2. **Budgeting.** Sample size is bounded in estimated *tokens*, not
   characters. Indic scripts tokenize far less efficiently than Latin, so a
   character cap is not a safe proxy for the model's context window.
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func

from kalanjiyam import database as db
from kalanjiyam.utils.ocr_types import ENGINE_LABELS, HTML_ENGINES, MARKDOWN_ENGINES

# --------------------------------------------------------------------------
# Budgets
#
# CONSERVATIVE by design. These are sized to stay comfortably inside
# llm-gemma's 32768-token context (system + input + output) without relying on
# an exact token count, because we do not run Gemma's tokenizer client-side.
#
# To raise them, calibrate rather than guess: the chat response carries
# `usage.prompt_tokens`, so sending known payloads per script yields real
# characters-per-token ratios for CHARS_PER_TOKEN below. The ceilings can
# likely go up substantially once measured.
# --------------------------------------------------------------------------

#: Total context window of the backing model, for reference.
MODEL_CONTEXT_TOKENS = 32768

#: Input budgets per persona, in estimated tokens.
INPUT_TOKEN_BUDGETS = {
    "front_matter": 8_000,
    "content_analysis": 12_000,
    "language_id": 2_000,
}

#: Estimated characters per token, by script. Latin tokenizes efficiently;
#: Indic scripts split conjuncts and matras into multiple tokens each, so we
#: assume a low (pessimistic) ratio, which makes us *overestimate* token count
#: and send less text than strictly necessary.
CHARS_PER_TOKEN = {
    "Latn": 3.0,
    "_default": 1.2,
}

#: Extra pessimism applied on top of the per-script ratio.
TOKEN_SAFETY_FACTOR = 0.85

#: Pages shorter than this are treated as blank and never sampled. Without
#: this, a partially-OCR'd large book yields mostly empty samples.
MIN_SAMPLE_CHARS = 50

#: Sampling tiers: (max_pages, front_matter_pages, body_samples). Front matter
#: widens with book size because large books usually print their own table of
#: contents, which gives whole-book structure without reading the whole book.
SAMPLING_TIERS = (
    (50, 5, 6),
    (300, 5, 12),
    (1000, 10, 16),
    (None, 15, 20),
)

#: Number of short samples handed to the language-id persona.
LANGUAGE_ID_SAMPLES = 3
LANGUAGE_ID_SAMPLE_CHARS = 1_000

#: Batch size for streaming page text out of the database.
_STREAM_BATCH = 250

#: Track key used for revisions predating the PageVersion table
#: (`Revision.page_version_id` is nullable).
LEGACY_TRACK = "legacy"

#: Tracks never used for metadata extraction: pulling a title or author out of
#: a translation would attribute the wrong bibliographic data to the book.
EXCLUDED_TRACK_PREFIXES = ("translation:", "TR:")

#: Quality tiers, lowest number wins. Mirrors the ordering in
#: `views.proofing.page.resolve_version_keys` but without its recency sort.
TIER_REVIEWED = 1
TIER_P2 = 2
TIER_P1 = 3
TIER_OCR = 4
TIER_UNKNOWN = 5

#: Tie-break order when a page has several `ocr:` tracks. This encodes
#: reproducibility, not a judgement about which engine is best -- we just need
#: repeated runs to pick the same track. Engines absent from this list sort
#: after it, alphabetically.
OCR_ENGINE_PREFERENCE = (
    "chandra",
    "dots_ocr",
    "glm_ocr",
    "qwen3",
    "deepseek",
    "nanonets",
    "surya",
    "tesseract_manuscript",
    "google",
    "tesseract",
)

#: Unicode ranges we bucket characters into, as (script_code, first, last).
#: Ordered most-specific first; the first matching range wins.
_SCRIPT_RANGES = (
    ("Taml", 0x0B80, 0x0BFF),
    ("Deva", 0x0900, 0x097F),
    ("Deva", 0xA8E0, 0xA8FF),
    ("Gran", 0x11300, 0x1137F),
    ("Beng", 0x0980, 0x09FF),
    ("Guru", 0x0A00, 0x0A7F),
    ("Gujr", 0x0A80, 0x0AFF),
    ("Orya", 0x0B00, 0x0B7F),
    ("Telu", 0x0C00, 0x0C7F),
    ("Knda", 0x0C80, 0x0CFF),
    ("Mlym", 0x0D00, 0x0D7F),
    ("Sinh", 0x0D80, 0x0DFF),
    ("Tibt", 0x0F00, 0x0FFF),
    ("Arab", 0x0600, 0x06FF),
    ("Arab", 0x0750, 0x077F),
    ("Latn", 0x0041, 0x005A),
    ("Latn", 0x0061, 0x007A),
    ("Latn", 0x00C0, 0x024F),
)

SCRIPT_LABELS = {
    "Taml": "Tamil",
    "Deva": "Devanagari",
    "Gran": "Grantha",
    "Beng": "Bengali",
    "Guru": "Gurmukhi",
    "Gujr": "Gujarati",
    "Orya": "Odia",
    "Telu": "Telugu",
    "Knda": "Kannada",
    "Mlym": "Malayalam",
    "Sinh": "Sinhala",
    "Tibt": "Tibetan",
    "Arab": "Arabic",
    "Latn": "Latin",
}


@dataclass
class TrackRow:
    """The latest revision on one (page, track) pair."""

    page_id: int
    revision_id: int
    version_key: str
    content_format: str
    char_len: int
    #: Populated from the Page table.
    order: int = 0
    slug: str = ""

    @property
    def is_ocr(self) -> bool:
        return self.version_key.startswith("ocr:")

    @property
    def engine(self) -> str | None:
        return self.version_key.split(":", 1)[1] if self.is_ocr else None


@dataclass
class SampleSet:
    """Pages chosen for one persona, plus how they were chosen."""

    rows: list[TrackRow] = field(default_factory=list)
    text: str = ""
    #: Page slugs, in the order they appear in `text`.
    page_slugs: list[str] = field(default_factory=list)
    #: How many sampled pages came from an unedited `ocr:` track.
    ocr_fallback_count: int = 0
    #: True when the token budget cut the sample short.
    truncated: bool = False

    @property
    def page_count(self) -> int:
        return len(self.rows)


# --------------------------------------------------------------------------
# Track resolution
# --------------------------------------------------------------------------


def _track_tier(version_key: str, moderator_ids: set[int]) -> int:
    """Rank a track by text quality. Lower is better."""
    if version_key.startswith("user:"):
        try:
            user_id = int(version_key.split(":", 1)[1])
        except ValueError:
            return TIER_P1
        return TIER_REVIEWED if user_id in moderator_ids else TIER_P1
    if version_key == "role:moderator":
        return TIER_REVIEWED
    if version_key == "role:p2":
        return TIER_P2
    if version_key in ("main", "role:p1", "original", LEGACY_TRACK):
        return TIER_P1
    if version_key.startswith("ocr:"):
        return TIER_OCR
    return TIER_UNKNOWN


def _ocr_engine_rank(version_key: str) -> tuple[int, str]:
    """Stable tie-break among `ocr:` tracks."""
    engine = version_key.split(":", 1)[1] if ":" in version_key else ""
    try:
        return (OCR_ENGINE_PREFERENCE.index(engine), engine)
    except ValueError:
        return (len(OCR_ENGINE_PREFERENCE), engine)


def latest_revisions_by_track(session, project_id: int) -> list[TrackRow]:
    """Return the newest revision on every (page, track) pair.

    One window-function query, and it selects `length(content)` rather than
    the content itself -- page selection must not materialize the whole book.
    """
    ranked = (
        session.query(
            db.Revision.id.label("revision_id"),
            db.Revision.page_id.label("page_id"),
            db.Revision.content_format.label("content_format"),
            func.length(db.Revision.content).label("char_len"),
            func.coalesce(db.PageVersion.version_key, LEGACY_TRACK).label(
                "version_key"
            ),
            func.row_number()
            .over(
                partition_by=(db.Revision.page_id, db.Revision.page_version_id),
                order_by=db.Revision.created.desc(),
            )
            .label("rn"),
        )
        .outerjoin(db.PageVersion, db.Revision.page_version_id == db.PageVersion.id)
        .filter(db.Revision.project_id == project_id)
        .subquery()
    )

    rows = [
        TrackRow(
            page_id=r.page_id,
            revision_id=r.revision_id,
            version_key=r.version_key,
            content_format=r.content_format or "plain",
            char_len=r.char_len or 0,
        )
        for r in session.query(ranked).filter(ranked.c.rn == 1).all()
    ]

    # Attach page ordering in a second small query.
    page_info = {
        page_id: (order, slug)
        for page_id, order, slug in session.query(
            db.Page.id, db.Page.order, db.Page.slug
        ).filter(db.Page.project_id == project_id)
    }
    for row in rows:
        order, slug = page_info.get(row.page_id, (0, ""))
        row.order = order
        row.slug = slug
    return rows


def _moderator_ids(session, rows: list[TrackRow]) -> set[int]:
    """Which `user:<id>` tracks belong to moderators (one query)."""
    user_ids = set()
    for row in rows:
        if row.version_key.startswith("user:"):
            try:
                user_ids.add(int(row.version_key.split(":", 1)[1]))
            except ValueError:
                continue
    if not user_ids:
        return set()

    users = session.query(db.User).filter(db.User.id.in_(user_ids)).all()
    return {
        u.id
        for u in users
        if u.is_moderator or u.is_org_admin or u.is_super_admin
    }


def resolve_extraction_tracks(session, project_id: int) -> dict[int, TrackRow]:
    """Pick the best text track for every page in the project.

    Precedence is strictly by quality: reviewed edits, then P2, then P1, then
    unedited OCR. Translation tracks are excluded outright.
    """
    rows = latest_revisions_by_track(session, project_id)
    rows = [
        r
        for r in rows
        if not r.version_key.startswith(EXCLUDED_TRACK_PREFIXES)
    ]
    mod_ids = _moderator_ids(session, rows)

    best: dict[int, TrackRow] = {}
    for row in rows:
        current = best.get(row.page_id)
        if current is None or _sort_key(row, mod_ids) < _sort_key(current, mod_ids):
            best[row.page_id] = row
    return best


def _sort_key(row: TrackRow, moderator_ids: set[int]):
    """Order tracks by tier, then OCR engine preference, then longest text."""
    return (
        _track_tier(row.version_key, moderator_ids),
        _ocr_engine_rank(row.version_key) if row.is_ocr else (-1, ""),
        -row.char_len,
        row.version_key,
    )


# --------------------------------------------------------------------------
# Text normalization
# --------------------------------------------------------------------------

_MD_MARKERS = re.compile(r"^\s{0,3}(#{1,6}\s+|>\s+|[-*+]\s+|\d+\.\s+)", re.MULTILINE)
_MD_INLINE = re.compile(r"(\*\*|__|\*|_|`{1,3}|~~)")
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def to_plain_text(content: str, content_format: str, version_key: str = "") -> str:
    """Strip markup so the model sees prose, not tags.

    Falling back to an HTML-emitting OCR track (nanonets, chandra) would
    otherwise hand the model raw markup, which reliably corrupts front-matter
    extraction.
    """
    if not content:
        return ""

    engine = version_key.split(":", 1)[1] if version_key.startswith("ocr:") else ""
    fmt = (content_format or "plain").lower()

    if fmt == "html" or engine in HTML_ENGINES:
        content = _TAG.sub(" ", content)
        content = html.unescape(content)
    elif fmt in ("markdown", "md") or engine in MARKDOWN_ENGINES:
        content = _MD_MARKERS.sub("", content)
        content = _MD_INLINE.sub("", content)

    content = _WS.sub(" ", content)
    content = _BLANKS.sub("\n\n", content)
    return content.strip()


# --------------------------------------------------------------------------
# Script profiling
# --------------------------------------------------------------------------


def _script_of(char: str) -> str | None:
    code = ord(char)
    for name, first, last in _SCRIPT_RANGES:
        if first <= code <= last:
            return name
    return None


def _stream_text(session, rows: list[TrackRow]):
    """Yield (row, plain_text) in batches, holding one batch in memory."""
    by_revision = {r.revision_id: r for r in rows}
    revision_ids = list(by_revision)
    for i in range(0, len(revision_ids), _STREAM_BATCH):
        chunk = revision_ids[i : i + _STREAM_BATCH]
        results = session.query(db.Revision.id, db.Revision.content).filter(
            db.Revision.id.in_(chunk)
        )
        for revision_id, content in results:
            row = by_revision[revision_id]
            yield row, to_plain_text(content, row.content_format, row.version_key)


def script_profile(session, rows: list[TrackRow]) -> dict:
    """Full-scan script mix plus cheap layout statistics.

    This is the only step that is genuinely O(pages), which is why it streams:
    a 2,000-page book is a few MB of text and never more than one batch is
    resident.

    Sampling would be cheaper but not trustworthy -- a Latin appendix in the
    last 200 pages of a Tamil book has to show up.
    """
    counts: Counter[str] = Counter()
    total_lines = 0
    short_lines = 0
    total_chars = 0

    for _row, text in _stream_text(session, rows):
        if not text:
            continue
        total_chars += len(text)
        for char in text:
            if char.isspace() or char.isdigit():
                continue
            if unicodedata.category(char).startswith("P"):
                continue
            script = _script_of(char)
            if script:
                counts[script] += 1

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            total_lines += 1
            if len(stripped) < 60:
                short_lines += 1

    total = sum(counts.values())
    scripts = (
        {name: round(n / total, 4) for name, n in counts.most_common()}
        if total
        else {}
    )
    return {
        "scripts": scripts,
        "total_chars": total_chars,
        # A rough layout signal, not a claim about metre: verse tends to
        # produce many short lines, prose few.
        "short_line_ratio": round(short_lines / total_lines, 4) if total_lines else 0.0,
    }


def dominant_script(scripts: dict) -> str:
    """The script with the largest share, or "Latn" when unknown."""
    return next(iter(scripts), "Latn")


# --------------------------------------------------------------------------
# Token estimation
# --------------------------------------------------------------------------


def _chars_per_token(scripts: dict | None) -> float:
    """The (pessimistic) characters-per-token ratio for this script mix."""
    script = dominant_script(scripts or {})
    ratio = CHARS_PER_TOKEN.get(script, CHARS_PER_TOKEN["_default"])
    return ratio * TOKEN_SAFETY_FACTOR


def estimate_tokens(text: str, scripts: dict | None = None) -> int:
    """Pessimistically estimate the token count of `text`.

    We do not run Gemma's tokenizer locally, so this deliberately errs high.
    See the budget note at the top of this module.
    """
    if not text:
        return 0
    return math.ceil(len(text) / _chars_per_token(scripts))


def _budget_chars(persona: str, scripts: dict | None) -> int:
    """Convert a persona's token budget into a character allowance.

    Floored against `estimate_tokens`' ceiling so that a sample filling the
    whole character budget still estimates at or under the token budget.
    """
    budget = INPUT_TOKEN_BUDGETS.get(persona, INPUT_TOKEN_BUDGETS["content_analysis"])
    return math.floor(budget * _chars_per_token(scripts))


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


def _tier_for(page_count: int) -> tuple[int, int]:
    for limit, front, body in SAMPLING_TIERS:
        if limit is None or page_count <= limit:
            return front, body
    return SAMPLING_TIERS[-1][1], SAMPLING_TIERS[-1][2]


def _usable(rows: list[TrackRow]) -> list[TrackRow]:
    """Ordered, non-blank pages."""
    return sorted(
        (r for r in rows if r.char_len >= MIN_SAMPLE_CHARS),
        key=lambda r: r.order,
    )


def _assemble(session, rows: list[TrackRow], budget_chars: int) -> SampleSet:
    """Fetch text for `rows` and concatenate until the budget is spent."""
    sample = SampleSet()
    texts = {row.revision_id: text for row, text in _stream_text(session, rows)}

    parts: list[str] = []
    used = 0
    for row in rows:
        text = texts.get(row.revision_id, "").strip()
        if not text:
            continue
        header = f"\n\n--- page {row.slug} ---\n"
        remaining = budget_chars - used - len(header)
        if remaining <= 0:
            sample.truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining]
            sample.truncated = True
        parts.append(header + text)
        used += len(header) + len(text)
        sample.rows.append(row)
        sample.page_slugs.append(row.slug)
        if row.is_ocr:
            sample.ocr_fallback_count += 1

    sample.text = "".join(parts).strip()
    return sample


def sample_front_matter(session, tracks: dict[int, TrackRow], scripts: dict) -> SampleSet:
    """The opening pages, where the title page and printed TOC live."""
    rows = _usable(list(tracks.values()))
    front, _body = _tier_for(len(rows))
    return _assemble(session, rows[:front], _budget_chars("front_matter", scripts))


def sample_body(session, tracks: dict[int, TrackRow], scripts: dict) -> SampleSet:
    """Evenly spaced body pages, plus the last few for the colophon.

    Cost here is flat in book size by construction: a 2,000-page book yields
    the same number of samples as a 200-page one. Coverage therefore falls as
    books grow, which is why `provenance` records the sampled page count and
    the tab shows it.
    """
    rows = _usable(list(tracks.values()))
    front, body = _tier_for(len(rows))

    middle = rows[front:]
    if not middle:
        middle = rows

    tail = middle[-3:] if len(middle) > 3 else []
    core = middle[: -3] if tail else middle

    chosen: list[TrackRow] = []
    if core:
        stride = max(1, len(core) // max(1, body - len(tail)))
        chosen = core[::stride][: body - len(tail)]
    chosen.extend(tail)

    chosen.sort(key=lambda r: r.order)
    return _assemble(session, chosen, _budget_chars("content_analysis", scripts))


def sample_for_language_id(
    session, tracks: dict[int, TrackRow], scripts: dict
) -> SampleSet:
    """A few short excerpts spread through the book."""
    rows = _usable(list(tracks.values()))
    if not rows:
        return SampleSet()

    step = max(1, len(rows) // (LANGUAGE_ID_SAMPLES + 1))
    chosen = [rows[min(step * (i + 1), len(rows) - 1)] for i in range(LANGUAGE_ID_SAMPLES)]

    # Deduplicate while preserving order (short books repeat pages).
    seen = set()
    unique = []
    for row in chosen:
        if row.page_id not in seen:
            seen.add(row.page_id)
            unique.append(row)

    budget = min(
        _budget_chars("language_id", scripts),
        LANGUAGE_ID_SAMPLE_CHARS * max(1, len(unique)),
    )
    return _assemble(session, unique, budget)


# --------------------------------------------------------------------------
# Derived stats
# --------------------------------------------------------------------------


def parse_languages(raw: str) -> list[dict]:
    """Parse the languages textarea: one ``code, script, role`` line each.

    Lenient by design -- a moderator typing just ``ta`` should work.
    """
    languages = []
    for line in (raw or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        code = parts[0] if parts else ""
        if not code:
            continue
        languages.append(
            {
                "code": code,
                "script": parts[1] if len(parts) > 1 and parts[1] else None,
                "role": parts[2] if len(parts) > 2 and parts[2] else None,
            }
        )
    return languages


def format_languages(languages: list[dict] | None) -> str:
    """Inverse of `parse_languages`."""
    lines = []
    for item in languages or []:
        if not isinstance(item, dict):
            continue
        parts = [
            str(item.get("code") or ""),
            str(item.get("script") or ""),
            str(item.get("role") or ""),
        ]
        while parts and not parts[-1]:
            parts.pop()
        if parts and parts[0]:
            lines.append(", ".join(parts))
    return "\n".join(lines)


def parse_toc(raw: str) -> list[dict]:
    """Parse the TOC textarea: one ``label | page`` entry per line."""
    entries = []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        label, _, page = line.partition("|")
        label = label.strip()
        if not label:
            continue
        entries.append({"label": label, "page": page.strip() or None})
    return entries


def format_toc(entries: list[dict] | None) -> str:
    """Inverse of `parse_toc`."""
    lines = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        page = str(entry.get("page") or "").strip()
        lines.append(f"{label} | {page}" if page else label)
    return "\n".join(lines)


def parse_list(raw: str) -> list[str]:
    """Parse a comma- or newline-separated list, preserving order."""
    items, seen = [], set()
    for chunk in re.split(r"[,\n]", raw or ""):
        value = chunk.strip()
        if value and value not in seen:
            seen.add(value)
            items.append(value)
    return items


def format_list(items: list[str] | None) -> str:
    return ", ".join(str(i) for i in (items or []) if str(i).strip())


# --------------------------------------------------------------------------
# Derived stats
# --------------------------------------------------------------------------


def derived_stats(session, project_id: int, tracks: dict[int, TrackRow]) -> dict:
    """Facts the model is never asked for, because we can just compute them."""
    all_rows = latest_revisions_by_track(session, project_id)
    engines = sorted(
        {r.engine for r in all_rows if r.is_ocr and r.engine},
    )

    pages_total = (
        session.query(func.count(db.Page.id))
        .filter(db.Page.project_id == project_id)
        .scalar()
        or 0
    )
    pages_with_text = len(tracks)
    pages_edited = sum(1 for r in tracks.values() if not r.is_ocr)

    profile = script_profile(session, list(tracks.values()))

    translations = (
        session.query(
            db.Translation.source_language,
            db.Translation.target_language,
            func.count(func.distinct(db.Translation.page_id)),
        )
        .join(db.Page, db.Translation.page_id == db.Page.id)
        .filter(db.Page.project_id == project_id)
        .group_by(db.Translation.source_language, db.Translation.target_language)
        .all()
    )

    return {
        "pages_total": pages_total,
        "pages_with_text": pages_with_text,
        "pages_edited": pages_edited,
        "pages_ocr_only": pages_with_text - pages_edited,
        "ocr_engines": engines,
        "ocr_engine_labels": [ENGINE_LABELS.get(e, e) for e in engines],
        "scripts": profile["scripts"],
        "script_labels": {
            code: SCRIPT_LABELS.get(code, code) for code in profile["scripts"]
        },
        "total_chars": profile["total_chars"],
        "short_line_ratio": profile["short_line_ratio"],
        "translation_pairs": [
            {"source": src or "auto", "target": tgt, "pages": count}
            for src, tgt, count in translations
        ],
        "computed_at": datetime.utcnow().isoformat(),
    }
