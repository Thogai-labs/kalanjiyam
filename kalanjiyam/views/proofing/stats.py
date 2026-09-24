"""Utilities for calculating project statistics.

These stats are useful for estimating the size of a project, which helps us
wtih cost estimation for our proofers.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from indic_transliteration import detect, sanscript

from kalanjiyam.database import Project

#: Matches whitespace spans.
RE_SPACE = re.compile(r"\s+", re.MULTILINE)
#: Matches Sanskrit vowels in SLP1.
RE_VOWEL = re.compile(r"[aAiIuUfFxXeEoO]")

#: Scripts that don't use aksharas
#: (copied from `indic_transliteration`)
ROMAN_SCHEMES = {
    "hk",
    "iast",
    "itrans",
    "kolkata_v2",
    "slp1",
    "velthuis",
}


@dataclass
class Stats:
    """Statistics for some project."""

    #: The number of pages.
    num_pages: int
    #: The number of words.
    #: Here, a "word" is a continuous span of characters with no whitespace.
    num_words: int
    #: The number of Roman characters.
    num_roman_characters: int
    #: The number of aksharas (syllables) in Devanagari, Kannada, or some other
    #: Brahmic script.
    num_aksharas: int


def _calculate_stats_for_strings(strings: Iterable[str]) -> Stats:
    num_pages = 0
    num_words = 0
    num_roman_characters = 0
    num_aksharas = 0
    for page_text in strings:
        num_pages += 1
        # N words will have n-1 spaces, so add 1 to get a better word count.
        spaces = RE_SPACE.findall(page_text)
        num_words += 1 + len(spaces)

        encoding = detect.detect(page_text)
        if encoding in ROMAN_SCHEMES:
            num_space_chars = sum(len(x) for x in spaces)
            num_roman_characters += len(page_text) - num_space_chars
        else:
            slp1_text = sanscript.transliterate(page_text, encoding, "slp1")
            num_aksharas += len(RE_VOWEL.findall(slp1_text))

    return Stats(
        num_pages=num_pages,
        num_words=num_words,
        num_roman_characters=num_roman_characters,
        num_aksharas=num_aksharas,
    )


def _iter_page_strings(project: Project) -> Iterable[str]:
    if not getattr(project, "id", None):
        for page in getattr(project, "pages", []):
            yield page.revisions[-1].content if getattr(page, "revisions", None) else ""
        return

    from sqlalchemy import func

    from kalanjiyam import database as db
    from kalanjiyam import queries as q

    session = q.get_session()
    subq = (
        session.query(
            db.Revision.page_id,
            func.max(db.Revision.id).label("max_id"),
        )
        .filter(db.Revision.project_id == project.id)
        .group_by(db.Revision.page_id)
        .subquery()
    )
    rows = (
        session.query(db.Page.id, db.Revision.content)
        .filter(db.Page.project_id == project.id)
        .outerjoin(subq, db.Page.id == subq.c.page_id)
        .outerjoin(db.Revision, db.Revision.id == subq.c.max_id)
        .order_by(db.Page.order)
        .all()
    )
    for _page_id, content in rows:
        yield content or ""


def calculate_stats(project: Project) -> Stats:
    return _calculate_stats_for_strings(_iter_page_strings(project))
