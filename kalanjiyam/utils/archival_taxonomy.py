"""The archival description taxonomy, as data.

SMOKE TEST ONLY. This module backs the "Metadata (test)" tab, which exists so we
can see what an ISAD(G) / ISAAR(CPF) / RiC-CM description looks like against a
real project before deciding whether to make it a data model. Nothing here is
persisted as columns, indexed, or exported. Delete the module, the template, and
the two routes to remove the experiment entirely.

The taxonomy is expressed as data rather than 23 hand-written template blocks so
that the rendering stays honest: every tag appears whether or not it has a value,
because seeing which tags stay empty against real records is the point.

Documents are keyed by tag code::

    {"REF": "...", "PER.ind": [{"label": ...}, ...], "REL": [{...}, ...]}
"""

from typing import NamedTuple

#: How a tag's value is rendered. The value shape follows from this:
#:
#: - ``text``      -> str, shown on one line
#: - ``prose``     -> str, shown as a paragraph
#: - ``entities``  -> list of access-point dicts (see ENTITY_KEYS)
#: - ``relations`` -> list of {subject, type, object, note} triples
KIND_TEXT = "text"
KIND_PROSE = "prose"
KIND_ENTITIES = "entities"
KIND_RELATIONS = "relations"

#: Recognised keys on an access-point object. Everything is optional except
#: ``label``. ``source`` distinguishes what the record actually says from what an
#: authority file contributed -- see the LOC.phys note in the taxonomy, where
#: coordinates are explicitly enrichment rather than original content.
ENTITY_KEYS = ("label", "variants", "dates", "auth_id", "source", "note")

#: Value of ``source`` marking a value that did NOT come from the record itself.
SOURCE_ENRICHMENT = "enrichment"


class Tag(NamedTuple):
    """One taxonomy tag."""

    #: The tag as it appears in the taxonomy table, e.g. "PER.ind".
    code: str
    #: Short human-readable name.
    label: str
    #: The standard and element number this implements.
    standard: str
    #: One of the KIND_* constants; drives rendering only.
    kind: str
    #: The taxonomy's own definition, shown as help text so each tag can be
    #: judged in place against real data.
    definition: str


#: The taxonomy, grouped for display. Order is the order it renders in.
GROUPS: list[tuple[str, list[Tag]]] = [
    (
        "Identity",
        [
            Tag(
                "REF",
                "Reference code",
                "ISAD(G) 1.1",
                KIND_TEXT,
                "The unique identifier of the unit of description, including "
                "legacy file/letter/telegram numbers.",
            ),
            Tag(
                "TITLE",
                "Title",
                "ISAD(G) 1.2",
                KIND_TEXT,
                "The name of the unit of description, taken from the record or "
                "supplied by the describer.",
            ),
            Tag(
                "DATE",
                "Dates of creation",
                "ISAD(G) 1.3",
                KIND_TEXT,
                "Creation/coverage dates, with archaic calendars (Hijri) "
                "normalized to ISO 8601.",
            ),
            Tag(
                "LEVEL",
                "Level of description",
                "ISAD(G) 1.4",
                KIND_TEXT,
                "The position of the unit in the hierarchy "
                "(fonds/series/file/item).",
            ),
            Tag(
                "EXTENT",
                "Extent & medium",
                "ISAD(G) 1.5",
                KIND_TEXT,
                "Quantity and physical/digital carrier of the material.",
            ),
        ],
    ),
    (
        "Context",
        [
            Tag(
                "CREATOR",
                "Creator & administrative history",
                "ISAD(G) 2.1-2.2",
                KIND_PROSE,
                "The agency/person that created the records, with its "
                "institutional or life history.",
            ),
            Tag(
                "CUSTHIST",
                "Archival / custodial history",
                "ISAD(G) 2.3",
                KIND_PROSE,
                "The chain of custody and transfers of the record over time.",
            ),
        ],
    ),
    (
        "Content & access",
        [
            Tag(
                "SCOPE",
                "Scope & content",
                "ISAD(G) 3.1",
                KIND_PROSE,
                "Narrative summary of the subject matter, actors, transactions "
                "and documentary contents.",
            ),
            Tag(
                "ACCESS",
                "Conditions of access & reproduction",
                "ISAD(G) 4.1-4.2",
                KIND_PROSE,
                "Legal/technical restrictions on consulting and copying the "
                "material.",
            ),
            Tag(
                "LANG",
                "Language / scripts of material",
                "ISAD(G) 4.3",
                KIND_ENTITIES,
                "Languages and writing systems present in the record.",
            ),
        ],
    ),
    (
        "Allied material & control",
        [
            Tag(
                "RELMAT",
                "Related units of description",
                "ISAD(G) 5.3",
                KIND_ENTITIES,
                "Other files, gazettes and counterpart series connected to this "
                "record.",
            ),
            Tag(
                "DESCTRL",
                "Description control & confidence",
                "ISAD(G) Area 7",
                KIND_PROSE,
                "Who described the record, by what rules and date, and with what "
                "confidence (OCR caveats).",
            ),
        ],
    ),
    (
        "Agents",
        [
            Tag(
                "PER.ind",
                "Person - individual",
                "ISAAR(CPF) / RiC Agent:Person",
                KIND_ENTITIES,
                "A single named historical actor, controlled as an authority "
                "access point with variant name forms.",
            ),
            Tag(
                "PER.fam",
                "Person - family / dynasty",
                "ISAAR(CPF) Family",
                KIND_ENTITIES,
                "A named family, lineage or ruling house treated as an authority "
                "entity.",
            ),
            Tag(
                "CORP",
                "Corporate body",
                "ISAAR(CPF) / RiC Agent:Group",
                KIND_ENTITIES,
                "A named organization - government office, department, regiment "
                "or corps.",
            ),
            Tag(
                "POSN",
                "Position",
                "RiC-CM Position",
                KIND_ENTITIES,
                "An office or rank held by a person within a corporate body, "
                "linked by relation rather than tagged as a separate "
                '"occupation" class.',
            ),
        ],
    ),
    (
        "Places",
        [
            Tag(
                "LOC.adm",
                "Administrative jurisdiction",
                "with existence dates",
                KIND_ENTITIES,
                "A politically defined territory, including extinct/deprecated "
                "jurisdictions qualified by dates of existence.",
            ),
            Tag(
                "LOC.phys",
                "Physical place / micro-toponym",
                "+ gazetteer enrichment",
                KIND_ENTITIES,
                "A camp, town, pass, building or natural feature; coordinates "
                "carried as flagged authority-file enrichment, not original "
                "content.",
            ),
        ],
    ),
    (
        "Terms",
        [
            Tag(
                "GENRE",
                "Genre / form term",
                "EAD genreform",
                KIND_ENTITIES,
                "The documentary form of items in the file - murasila, kharita, "
                "D.O. letter, telegram, gazette notification, minute.",
            ),
            Tag(
                "SUBJ",
                "Subject access point",
                "controlled topical vocabulary",
                KIND_ENTITIES,
                "Topical and demographic descriptors applied with controlled "
                "vocabulary and reparative-description caution for colonial "
                "terminology.",
            ),
            Tag(
                "RULE",
                "Rule / mandate",
                "RiC-CM Rule",
                KIND_ENTITIES,
                "The regulation, statute or authoritative instruction under "
                "which the documented actions were taken.",
            ),
        ],
    ),
    (
        "Graph",
        [
            Tag(
                "REL",
                "Relations",
                "RiC-CM relations",
                KIND_RELATIONS,
                "Typed links (kinship, succession, authorship, beneficiary, "
                "sanction, governance) modeled as first-class relations rather "
                "than separate taxonomy classes.",
            ),
            Tag(
                "AUTH.id",
                "Authority identifiers",
                "VIAF / LCNAF / SNAC / Wikidata",
                KIND_ENTITIES,
                "External persistent identifier attached to each access point.",
            ),
        ],
    ),
]

#: Flat view, for lookups and for counting coverage.
TAGS: list[Tag] = [tag for _, tags in GROUPS for tag in tags]

#: Tag code -> Tag.
BY_CODE: dict[str, Tag] = {tag.code: tag for tag in TAGS}


def is_empty(value) -> bool:
    """True if a tag has no usable value.

    Empty strings, empty lists and None all count as absent, so the template can
    show a consistent placeholder rather than three different kinds of blank.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def coverage(document: dict | None) -> dict:
    """Count how many tags the document actually fills.

    This is the number the smoke test exists to produce: against a real record,
    how much of the taxonomy is reachable?
    """
    document = document or {}
    filled = [tag.code for tag in TAGS if not is_empty(document.get(tag.code))]
    return {
        "filled": len(filled),
        "total": len(TAGS),
        "filled_codes": filled,
        "empty_codes": [tag.code for tag in TAGS if tag.code not in set(filled)],
    }


def normalize_entities(value) -> list[dict]:
    """Coerce a tag value into a list of access-point dicts.

    Hand-written and LLM-produced JSON both tend to give a bare string or a list
    of strings where a list of objects is expected. Accept all three rather than
    rendering nothing, since the point of the tab is to look at whatever arrives.
    """
    if is_empty(value):
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict):
        value = [value]

    out = []
    for item in value:
        if isinstance(item, str):
            out.append({"label": item})
        elif isinstance(item, dict):
            entry = {k: v for k, v in item.items() if not is_empty(v)}
            if "label" not in entry:
                # Tolerate the common alternate spellings rather than dropping
                # the row on the floor.
                for alt in ("name", "value", "term", "title"):
                    if alt in entry:
                        entry["label"] = entry.pop(alt)
                        break
            if entry.get("label"):
                out.append(entry)
    return out


def normalize_relations(value) -> list[dict]:
    """Coerce a REL value into subject/type/object triples."""
    if is_empty(value):
        return []
    if isinstance(value, dict):
        value = [value]

    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        triple = {
            "subject": item.get("subject") or item.get("from") or "",
            "type": item.get("type") or item.get("relation") or "",
            "object": item.get("object") or item.get("to") or "",
            "note": item.get("note") or "",
        }
        if triple["subject"] or triple["object"]:
            out.append(triple)
    return out


#: A fully-populated example, so the tab renders something on first load without
#: needing the LLM service (whose personas are not currently registered). The
#: material is the Baluchistan Agency / Kalat State correspondence the taxonomy
#: was evidently written against.
SAMPLE: dict = {
    "REF": "IOR/R/1/34/17; A.G.G. Baluchistan D.O. No. 412-C of 1932",
    "TITLE": (
        "Grant of an honorary commission to Lt. Shahzada Ahmad Yar Khan, "
        "Kalat State"
    ),
    "DATE": "1932-03-11/1933-01-27 (1350-1351 AH)",
    "LEVEL": "file",
    "EXTENT": "64 leaves; mixed typescript and manuscript, 1 telegram flimsy",
    "CREATOR": (
        "Office of the Agent to the Governor-General in Baluchistan (A.G.G.), "
        "Quetta. Established 1877 under the Treaty of Jacobabad, the A.G.G. "
        "exercised the Governor-General's political authority over the "
        "Baluchistan Agency and relations with the princely states of Kalat and "
        "Las Bela, reporting to the Foreign and Political Department of the "
        "Government of India."
    ),
    "CUSTHIST": (
        "Retained in the A.G.G. Quetta political record room until 1947; "
        "transferred with the Baluchistan Agency residuary records; "
        "microfilmed 1974; digitised from the microfilm in 2019. The original "
        "enclosure listed at folio 12 was not present at the time of filming."
    ),
    "SCOPE": (
        "Correspondence concerning the proposal to confer an honorary "
        "commission in the Indian Army on Lt. Shahzada Ahmad Yar Khan, heir "
        "apparent of Kalat. Contains the initiating murasila from the Khan of "
        "Kalat, the A.G.G.'s demi-official minute of 11 March 1932, a telegram "
        "from the Foreign and Political Department seeking precedent, and the "
        "final gazette notification. Also documents the associated contribution "
        "to the Baluchistan war funds."
    ),
    "ACCESS": (
        "Open. Digital surrogate available; original held offsite and requires "
        "72 hours' notice. Reproduction permitted for non-commercial research "
        "with attribution."
    ),
    "LANG": [
        {"label": "English", "auth_id": "iso639-3:eng", "note": "typescript"},
        {
            "label": "Persian",
            "auth_id": "iso639-3:fas",
            "note": "Nasta'liq, folios 3-7",
        },
        {"label": "Urdu", "auth_id": "iso639-3:urd", "note": "Shikasta, folio 21"},
    ],
    "RELMAT": [
        {
            "label": "Gazette of India, 27 January 1933, Part I, p. 114",
            "note": "notification of the commission",
        },
        {
            "label": "IOR/L/PS/12/3201",
            "note": "Political Department counterpart file",
        },
    ],
    "DESCTRL": (
        "Described by V. Iyer, 2026-08-12, to ISAD(G) 2nd ed. and ISAAR(CPF) "
        "2nd ed. Descriptive content derived from OCR of the digital surrogate; "
        "confidence medium. Personal names in the Persian folios were read from "
        "Nasta'liq with low OCR confidence and have not been verified against "
        "the original."
    ),
    "PER.ind": [
        {
            "label": "Ahmad Yar Khan, Shahzada, 1904-1979",
            "variants": [
                "Lt. Shahzada Ahmad Yar Khan",
                "H.H. the Khan of Kalat",
                "Mir Ahmad Yar Khan Ahmadzai",
            ],
            "dates": "1904-1979",
            "auth_id": "viaf:12345678",
            "source": "record",
        },
        {
            "label": "Cater, Sir Arthur Nicholas Lockhart, 1885-1958",
            "variants": ["A. N. L. Cater", "The A.G.G."],
            "dates": "1885-1958",
            "auth_id": "viaf:87654321",
            "source": "record",
        },
    ],
    "PER.fam": [
        {
            "label": "Ahmadzai dynasty",
            "variants": ["House of Kalat", "Ahmadzai Khans"],
            "dates": "1666-1955",
            "source": "record",
        }
    ],
    "CORP": [
        {
            "label": "Agent to the Governor-General in Baluchistan",
            "variants": ["A.G.G. Baluchistan"],
            "dates": "1877-1947",
            "source": "record",
        },
        {
            "label": "Foreign and Political Department, Government of India",
            "dates": "1914-1937",
            "source": "record",
        },
        {"label": "124th Duchess of Connaught's Own Baluchistan Infantry"},
    ],
    "POSN": [
        {
            "label": "Agent to the Governor-General in Baluchistan",
            "note": "held by Cater, 1931-1936",
        },
        {"label": "Honorary Lieutenant, Indian Army"},
    ],
    "LOC.adm": [
        {
            "label": "Kalat State",
            "dates": "1666-1955",
            "note": "princely state; acceded 1948, merged 1955",
            "source": "record",
        },
        {"label": "Baluchistan Agency", "dates": "1877-1947", "source": "record"},
    ],
    "LOC.phys": [
        {
            "label": "Camp Dhadar",
            "source": "record",
            "note": "A.G.G.'s cold-weather camp",
        },
        {
            "label": "Kalat House, Quetta",
            "auth_id": "wikidata:Q2477346",
            "dates": "30.1798 N, 66.9750 E",
            "source": SOURCE_ENRICHMENT,
            "note": "coordinates from gazetteer, not stated in the record",
        },
    ],
    "GENRE": [
        {"label": "murasila", "note": "folios 3-7, Persian"},
        {"label": "demi-official letter"},
        {"label": "telegram"},
        {"label": "gazette notification"},
        {"label": "minute"},
    ],
    "SUBJ": [
        {"label": "Honorary commissions - India"},
        {"label": "War funds - Baluchistan"},
        {"label": "Princely states - succession"},
        {
            "label": "Baloch",
            "note": "ethnonym as used in the record; see reparative "
            "description statement",
        },
    ],
    "RULE": [
        {
            "label": "Indian Army Act, 1911, s. 4",
            "note": "cited as the basis for honorary rank",
        },
        {"label": "Treaty of Jacobabad, 1876"},
    ],
    "REL": [
        {
            "subject": "Ahmad Yar Khan, Shahzada",
            "type": "isHeirOf",
            "object": "Ahmadzai dynasty",
            "note": "heir apparent at the time of the file",
        },
        {
            "subject": "Cater, Sir Arthur Nicholas Lockhart",
            "type": "holdsPosition",
            "object": "Agent to the Governor-General in Baluchistan",
            "note": "1931-1936",
        },
        {
            "subject": "Foreign and Political Department",
            "type": "sanctioned",
            "object": "Grant of honorary commission",
            "note": "per telegram of 1932-11-04",
        },
        {
            "subject": "IOR/R/1/34/17",
            "type": "governedBy",
            "object": "Indian Army Act, 1911, s. 4",
        },
        {
            "subject": "Ahmad Yar Khan, Shahzada",
            "type": "isBeneficiaryOf",
            "object": "Grant of honorary commission",
        },
    ],
    "AUTH.id": [
        {
            "label": "viaf:12345678",
            "note": "Ahmad Yar Khan",
            "source": SOURCE_ENRICHMENT,
        },
        {
            "label": "wikidata:Q2477346",
            "note": "Kalat House, Quetta",
            "source": SOURCE_ENRICHMENT,
        },
        {
            "label": "lcnaf:n79021383",
            "note": "Kalat State",
            "source": SOURCE_ENRICHMENT,
        },
    ],
}
