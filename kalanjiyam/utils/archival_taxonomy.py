"""The archival description taxonomy, as data.

This is the client-supplied description schema for the archival portal, expressed
as ISAD(G) / ISAAR(CPF) / RiC-CM elements. It backs both the description tab and
the extraction request sent to ``/v1/metadata`` -- `build_prompt` and the request
builder read the same `GROUPS`, so the instruction can never drift from what the
tab renders.

Documents are keyed by tag code::

    {"REFERENCE": "...", "PERSON NAME": [{"label": ...}, ...], "RELATION": [{...}]}

Three tags are **write-locked**: they describe custody and access, which are
recorded in the accession file and appear nowhere in the page text. They are
excluded from every request, so a model has no opportunity to invent a provenance
chain. See `WRITE_LOCKED`.

Codes were renamed from the earlier short-tag form (``REF``, ``PER.ind``) to the
client's names. `LEGACY_CODES` maps the old spelling to the new one so documents
stored under the old keys still resolve.
"""

from typing import NamedTuple

#: Version of this schema, echoed in every extraction request and recorded on
#: every run. A run that does not say which schema it answered cannot be audited.
TAXONOMY_VERSION = "client-2026-08"

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
#: ``label`` and ``source``. ``evidence`` carries the spans that justify the value
#: and is required whenever ``source`` is `SOURCE_RECORD` -- a claimed fact with
#: no citation is indistinguishable from an invented one.
ENTITY_KEYS = (
    "label",
    "variants",
    "dates",
    "auth_id",
    "kind",
    "source",
    "evidence",
    "note",
)

#: Where a value came from. Every value must declare one.
#:
#: The distinction is not bookkeeping: only `SOURCE_RECORD` values can be verified
#: against the page text, and only verified values are safe to publish. Marking a
#: synthesised summary as `SOURCE_DERIVED` is what keeps the verification rate
#: honest -- a summary cannot quote a single span, and pretending otherwise would
#: make the metric meaningless.
SOURCE_RECORD = "record"  # stated in the document text; must carry a quote
SOURCE_DERIVED = "derived"  # synthesised or computed; cites contributing pages
SOURCE_ENRICHMENT = "enrichment"  # from an external authority file
SOURCE_CURATED = "curated"  # typed by an archivist

SOURCES = (SOURCE_RECORD, SOURCE_DERIVED, SOURCE_ENRICHMENT, SOURCE_CURATED)

#: Subtypes for PLACE. The client's schema has a single PLACE tag, but the
#: administrative/physical distinction still decides whether gazetteer
#: coordinates are legitimate enrichment or noise, so it survives as a subtype.
PLACE_ADMINISTRATIVE = "administrative"
PLACE_PHYSICAL = "physical"


class Tag(NamedTuple):
    """One taxonomy tag."""

    #: The tag as it appears in the schema, e.g. "PERSON NAME".
    code: str
    #: Short human-readable name.
    label: str
    #: The standard and element number this implements.
    standard: str
    #: One of the KIND_* constants; drives rendering only.
    kind: str
    #: The client's own definition, shown as help text and sent as the tag's
    #: instruction in the extraction prompt.
    definition: str


#: The taxonomy, grouped for display. Order is the order it renders in.
GROUPS: list[tuple[str, list[Tag]]] = [
    (
        "Identity",
        [
            Tag(
                "REFERENCE",
                "Reference code",
                "ISAD(G) 1.1",
                KIND_TEXT,
                "The identifying numbers for the file -- both the archive's own "
                "catalogue code and the numbers the original office stamped on "
                "its letters, telegrams and memos.",
            ),
            Tag(
                "TITLE",
                "Title",
                "ISAD(G) 1.2",
                KIND_TEXT,
                "The name of the file -- either printed on it, or written by the "
                "archivist when there is none.",
            ),
            Tag(
                "DATE",
                "Dates of creation",
                "ISAD(G) 1.3",
                KIND_TEXT,
                "When the material was made or accumulated, in ordinary Gregorian "
                "years; any Hijri or other calendar date is noted alongside. "
                "Give inclusive dates spanning the material (e.g. 1921-1950), "
                "not a single date.",
            ),
            Tag(
                "EXTENT",
                "Extent & medium",
                "ISAD(G) 1.5",
                KIND_TEXT,
                "How much there is and in what physical form -- leaves, boxes, "
                "reels, bytes.",
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
                "Who made and kept the file -- normally the office rather than "
                "the individual signatory -- together with background on what "
                "that office or person did.",
            ),
            Tag(
                "CUSTODIAL HISTORY",
                "Archival / custodial history",
                "ISAD(G) 2.3",
                KIND_PROSE,
                "Who has held the file since it left the creator's hands, and how "
                "it reached the archive.",
            ),
        ],
    ),
    (
        "Content & access",
        [
            Tag(
                "SCOPE CONTENT",
                "Scope & content",
                "ISAD(G) 3.1",
                KIND_PROSE,
                "A summary of what is actually inside -- the story, the people, "
                "the transactions, the kinds of documents.",
            ),
            Tag(
                "ACCESS",
                "Conditions of access & reproduction",
                "ISAD(G) 4.1-4.2",
                KIND_PROSE,
                "Whether anyone may consult the material, copy or publish it, and "
                "any technical barrier (encryption, obsolete format) to reading it.",
            ),
            Tag(
                "LANGUAGE",
                "Language / scripts of material",
                "ISAD(G) 4.3",
                KIND_ENTITIES,
                "Which languages and which scripts appear in the material -- e.g. "
                "Persian in Nasta'liq, Urdu in Shikasta.",
            ),
        ],
    ),
    (
        "Allied material & control",
        [
            Tag(
                "RELATED MATERIAL",
                "Related units of description",
                "ISAD(G) 5.3",
                KIND_ENTITIES,
                "Other files, originals, copies or publications elsewhere that "
                "belong with this one.",
            ),
            Tag(
                "DESCRIPTION",
                "Description control & confidence",
                "ISAD(G) Area 7",
                KIND_PROSE,
                "Who wrote the description, when, using which rules and sources -- "
                "including how confident they are and where the evidence is weak.",
            ),
        ],
    ),
    (
        "Agents",
        [
            Tag(
                "PERSON NAME",
                "Person - individual",
                "ISAAR(CPF) / RiC Agent:Person",
                KIND_ENTITIES,
                "One named individual, recorded in a single standard form with all "
                "their variant names and titles gathered under it.",
            ),
            Tag(
                "FAMILY NAME",
                "Person - family / dynasty",
                "ISAAR(CPF) Family",
                KIND_ENTITIES,
                "A family, lineage, house, dynasty, clan or tribe treated as a "
                "single named entity.",
            ),
            Tag(
                "CORPORATE BODY NAME",
                "Corporate body",
                "ISAAR(CPF) / RiC Agent:Group",
                KIND_ENTITIES,
                "An organisation acting as a body -- a department, office, "
                "regiment, corps or council.",
            ),
            Tag(
                "POSITION",
                "Position",
                "RiC-CM Position",
                KIND_ENTITIES,
                "An office or rank itself, separate from whoever held it, so you "
                'can ask "who held this post in 1932?" as well as "what did this '
                'person do?"',
            ),
        ],
    ),
    (
        "Places",
        [
            Tag(
                "PLACE",
                "Place",
                "with existence dates + gazetteer enrichment",
                KIND_ENTITIES,
                "Any named place -- a state, jurisdiction, town, camp, pass or "
                "building -- with start and end dates if it no longer exists, plus "
                "coordinates where useful. Set `kind` to "
                f'"{PLACE_ADMINISTRATIVE}" or "{PLACE_PHYSICAL}". Coordinates are '
                "authority-file enrichment, not original content.",
            ),
        ],
    ),
    (
        "Terms",
        [
            Tag(
                "DOCUMENT FORMAT",
                "Genre / form term",
                "EAD genreform",
                KIND_ENTITIES,
                "The kind of document something is -- letter, telegram, minute, "
                "gazette notice, murasila, kharita.",
            ),
            Tag(
                "SUBJECT",
                "Subject access point",
                "controlled topical vocabulary",
                KIND_ENTITIES,
                "What the material is about, expressed in standard topic terms so "
                "it can be found alongside similar material elsewhere.",
            ),
            Tag(
                "EVENT",
                "Event",
                "RiC-CM Event",
                KIND_ENTITIES,
                "A dated happening the records document or refer to, such as a "
                "war, an accession or a ceremony.",
            ),
            Tag(
                "RULE",
                "Rule / mandate",
                "RiC-CM Rule",
                KIND_ENTITIES,
                "The regulation, law or standing instruction that governed the "
                "action, and the authority it conferred on someone to act.",
            ),
        ],
    ),
    (
        "Graph",
        [
            Tag(
                "RELATION",
                "Relations",
                "RiC-CM relations",
                KIND_RELATIONS,
                "An explicit, typed link between two entities -- parent of, "
                "successor to, author of, governed by -- so the description "
                "becomes a connected network rather than a flat list.",
            ),
            Tag(
                "AUTHOR ID",
                "Authority identifiers",
                "VIAF / LCNAF / SNAC / Wikidata",
                KIND_ENTITIES,
                "A permanent external ID for a person, family, body or place, "
                "linking your entry to the same entity in other catalogues "
                "worldwide.",
            ),
        ],
    ),
]

#: Flat view, for lookups and for counting coverage.
TAGS: list[Tag] = [tag for _, tags in GROUPS for tag in tags]

#: Tag code -> Tag.
BY_CODE: dict[str, Tag] = {tag.code: tag for tag in TAGS}

#: Tags never sent to the extraction service.
#:
#: These record custody and access. Nothing in the page text supports them, and a
#: model asked for a custodial history will produce a fluent invention. Excluding
#: them from the request is a lock in the contract rather than a rule in a prompt.
WRITE_LOCKED = frozenset({"REFERENCE", "CUSTODIAL HISTORY", "ACCESS"})

#: Old short-tag spelling -> current code, for documents stored before the rename.
#: The two place tags collapse into one; `LEVEL` was dropped from the schema.
LEGACY_CODES = {
    "REF": "REFERENCE",
    "CUSTHIST": "CUSTODIAL HISTORY",
    "SCOPE": "SCOPE CONTENT",
    "LANG": "LANGUAGE",
    "RELMAT": "RELATED MATERIAL",
    "DESCTRL": "DESCRIPTION",
    "PER.ind": "PERSON NAME",
    "PER.fam": "FAMILY NAME",
    "CORP": "CORPORATE BODY NAME",
    "POSN": "POSITION",
    "LOC.adm": "PLACE",
    "LOC.phys": "PLACE",
    "GENRE": "DOCUMENT FORMAT",
    "SUBJ": "SUBJECT",
    "AUTH.id": "AUTHOR ID",
}


def extractable_tags() -> list[Tag]:
    """The tags an extraction request may ask for -- everything but `WRITE_LOCKED`."""
    return [tag for tag in TAGS if tag.code not in WRITE_LOCKED]


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
    """Count how many tags the document actually fills."""
    document = document or {}
    filled = [tag.code for tag in TAGS if not is_empty(document.get(tag.code))]
    return {
        "filled": len(filled),
        "total": len(TAGS),
        "filled_codes": filled,
        "empty_codes": [tag.code for tag in TAGS if tag.code not in set(filled)],
    }


def migrate_document(document: dict | None) -> dict:
    """Rewrite a document stored under the old short-tag codes.

    Values under a legacy key move to the current code. The two place tags merge,
    so their entity lists are concatenated rather than one overwriting the other;
    each entity keeps a `kind` recording which tag it came from. Keys already
    using the current spelling win over a legacy key naming the same tag.
    """
    document = document or {}
    out: dict = {}

    for key, value in document.items():
        if key in BY_CODE:
            out[key] = value

    place_kinds = {"LOC.adm": PLACE_ADMINISTRATIVE, "LOC.phys": PLACE_PHYSICAL}
    for old, new in LEGACY_CODES.items():
        if old not in document or is_empty(document[old]):
            continue
        if new in out and old not in place_kinds:
            continue

        value = document[old]
        if old in place_kinds:
            entities = normalize_entities(value)
            for entity in entities:
                entity.setdefault("kind", place_kinds[old])
            out["PLACE"] = out.get("PLACE", []) + entities
        else:
            out[new] = value

    return out


def normalize_entities(value) -> list[dict]:
    """Coerce a tag value into a list of access-point dicts.

    Hand-written and model-produced JSON both tend to give a bare string or a list
    of strings where a list of objects is expected. Accept all three rather than
    dropping the value.
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
    """Coerce a RELATION value into subject/type/object triples."""
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
        for key in ("source", "evidence"):
            if not is_empty(item.get(key)):
                triple[key] = item[key]
        if triple["subject"] or triple["object"]:
            out.append(triple)
    return out


#: Shape hint per kind, used to build the instruction block.
_SHAPE = {
    KIND_TEXT: '{"value": str, "confidence": float, "source": str, '
    '"evidence": [{"page_slug": str, "block_id": str, "quote": str}]}',
    KIND_PROSE: '{"value": str (a paragraph), "confidence": float, "source": str, '
    '"evidence": [...]}',
    KIND_ENTITIES: '{"confidence": float, "value": [{"label": str, '
    '"variants": [str], "dates": str, "auth_id": str, "source": str, '
    '"evidence": [{"page_slug": str, "block_id": str, "quote": str}]}]}',
    KIND_RELATIONS: '{"confidence": float, "value": [{"subject": str, '
    '"type": str, "object": str, "note": str, "source": str, "evidence": [...]}]}',
}


def build_prompt(codes: list[str] | None = None) -> str:
    """Build the instruction block describing the requested tags.

    Generated from `GROUPS` so the instruction cannot drift from what the tab
    renders. `WRITE_LOCKED` tags are never included, whatever `codes` asks for.
    """
    wanted = set(codes) if codes else None
    lines = [
        "You are an archivist describing a historical record to ISAD(G), "
        "ISAAR(CPF) and RiC-CM.",
        "",
        "Read the document text below and return a SINGLE JSON object keyed by "
        "the tag codes listed here. No prose, no markdown fences, JSON only.",
        "",
    ]
    for group_name, tags in GROUPS:
        selected = [
            t
            for t in tags
            if t.code not in WRITE_LOCKED and (wanted is None or t.code in wanted)
        ]
        if not selected:
            continue
        lines.append(f"## {group_name}")
        for tag in selected:
            lines.append(
                f'"{tag.code}": {_SHAPE[tag.kind]}  // {tag.label} '
                f"({tag.standard}). {tag.definition}"
            )
        lines.append("")

    lines += [
        "Rules:",
        "- Omit a tag entirely rather than inventing a plausible value. "
        "Declining is correct behaviour and is measured separately from failure.",
        "- Every value must carry `source`: "
        f'"{SOURCE_RECORD}" (stated in the text -- a verbatim `quote` is then '
        f'REQUIRED), "{SOURCE_DERIVED}" (synthesised or computed; cite the '
        "contributing page_slugs and omit the quote), or "
        f'"{SOURCE_ENRICHMENT}" (from an external authority file).',
        "- A `quote` must appear verbatim in the block text you were given. "
        "Quotes are checked against the source; a fabricated one scores worse "
        "than a declined field.",
        "- Cite `block_id` from the blocks you were given, so the value can be "
        "traced back to a region of the page image.",
        "- For access points, put the form used in the record in `label` and "
        "any other forms seen in `variants`.",
        "- Only set `auth_id` if an identifier appears in the text itself. "
        "Do not invent VIAF, LCNAF or Wikidata identifiers.",
        "- Normalize Hijri dates to ISO 8601 where possible, keeping the "
        "original in parentheses.",
        "- The text may be noisy OCR. Prefer declining a tag over guessing at "
        "an unreadable name.",
        "",
        "--- DOCUMENT TEXT ---",
    ]
    return "\n".join(lines)


#: A fully-populated example, so the tab renders something without a live
#: extraction service. The material is the Baluchistan Agency / Kalat State
#: correspondence the taxonomy was written against.
SAMPLE: dict = {
    "REFERENCE": "IOR/R/1/34/17; A.G.G. Baluchistan D.O. No. 412-C of 1932",
    "TITLE": (
        "Grant of an honorary commission to Lt. Shahzada Ahmad Yar Khan, " "Kalat State"
    ),
    "DATE": "1932-03-11/1933-01-27 (1350-1351 AH)",
    "EXTENT": "64 leaves; mixed typescript and manuscript, 1 telegram flimsy",
    "CREATOR": (
        "Office of the Agent to the Governor-General in Baluchistan (A.G.G.), "
        "Quetta. Established 1877 under the Treaty of Jacobabad, the A.G.G. "
        "exercised the Governor-General's political authority over the "
        "Baluchistan Agency and relations with the princely states of Kalat and "
        "Las Bela, reporting to the Foreign and Political Department of the "
        "Government of India."
    ),
    "CUSTODIAL HISTORY": (
        "Retained in the A.G.G. Quetta political record room until 1947; "
        "transferred with the Baluchistan Agency residuary records; "
        "microfilmed 1974; digitised from the microfilm in 2019. The original "
        "enclosure listed at folio 12 was not present at the time of filming."
    ),
    "SCOPE CONTENT": (
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
    "LANGUAGE": [
        {"label": "English", "auth_id": "iso639-3:eng", "note": "typescript"},
        {
            "label": "Persian",
            "auth_id": "iso639-3:fas",
            "note": "Nasta'liq, folios 3-7",
        },
        {"label": "Urdu", "auth_id": "iso639-3:urd", "note": "Shikasta, folio 21"},
    ],
    "RELATED MATERIAL": [
        {
            "label": "Gazette of India, 27 January 1933, Part I, p. 114",
            "note": "notification of the commission",
        },
        {
            "label": "IOR/L/PS/12/3201",
            "note": "Political Department counterpart file",
        },
    ],
    "DESCRIPTION": (
        "Described by V. Iyer, 2026-08-12, to ISAD(G) 2nd ed. and ISAAR(CPF) "
        "2nd ed. Descriptive content derived from OCR of the digital surrogate; "
        "confidence medium. Personal names in the Persian folios were read from "
        "Nasta'liq with low OCR confidence and have not been verified against "
        "the original."
    ),
    "PERSON NAME": [
        {
            "label": "Ahmad Yar Khan, Shahzada, 1904-1979",
            "variants": [
                "Lt. Shahzada Ahmad Yar Khan",
                "H.H. the Khan of Kalat",
                "Mir Ahmad Yar Khan Ahmadzai",
            ],
            "dates": "1904-1979",
            "auth_id": "viaf:12345678",
            "source": SOURCE_RECORD,
        },
        {
            "label": "Cater, Sir Arthur Nicholas Lockhart, 1885-1958",
            "variants": ["A. N. L. Cater", "The A.G.G."],
            "dates": "1885-1958",
            "auth_id": "viaf:87654321",
            "source": SOURCE_RECORD,
        },
    ],
    "FAMILY NAME": [
        {
            "label": "Ahmadzai dynasty",
            "variants": ["House of Kalat", "Ahmadzai Khans"],
            "dates": "1666-1955",
            "source": SOURCE_RECORD,
        }
    ],
    "CORPORATE BODY NAME": [
        {
            "label": "Agent to the Governor-General in Baluchistan",
            "variants": ["A.G.G. Baluchistan"],
            "dates": "1877-1947",
            "source": SOURCE_RECORD,
        },
        {
            "label": "Foreign and Political Department, Government of India",
            "dates": "1914-1937",
            "source": SOURCE_RECORD,
        },
        {"label": "124th Duchess of Connaught's Own Baluchistan Infantry"},
    ],
    "POSITION": [
        {
            "label": "Agent to the Governor-General in Baluchistan",
            "note": "held by Cater, 1931-1936",
        },
        {"label": "Honorary Lieutenant, Indian Army"},
    ],
    "PLACE": [
        {
            "label": "Kalat State",
            "kind": PLACE_ADMINISTRATIVE,
            "dates": "1666-1955",
            "note": "princely state; acceded 1948, merged 1955",
            "source": SOURCE_RECORD,
        },
        {
            "label": "Baluchistan Agency",
            "kind": PLACE_ADMINISTRATIVE,
            "dates": "1877-1947",
            "source": SOURCE_RECORD,
        },
        {
            "label": "Camp Dhadar",
            "kind": PLACE_PHYSICAL,
            "source": SOURCE_RECORD,
            "note": "A.G.G.'s cold-weather camp",
        },
        {
            "label": "Kalat House, Quetta",
            "kind": PLACE_PHYSICAL,
            "auth_id": "wikidata:Q2477346",
            "dates": "30.1798 N, 66.9750 E",
            "source": SOURCE_ENRICHMENT,
            "note": "coordinates from gazetteer, not stated in the record",
        },
    ],
    "DOCUMENT FORMAT": [
        {"label": "murasila", "note": "folios 3-7, Persian"},
        {"label": "demi-official letter"},
        {"label": "telegram"},
        {"label": "gazette notification"},
        {"label": "minute"},
    ],
    "SUBJECT": [
        {"label": "Honorary commissions - India"},
        {"label": "War funds - Baluchistan"},
        {"label": "Princely states - succession"},
        {
            "label": "Baloch",
            "note": "ethnonym as used in the record; see reparative "
            "description statement",
        },
    ],
    "EVENT": [
        {
            "label": "Grant of honorary commission to the heir apparent of Kalat",
            "dates": "1933-01-27",
            "source": SOURCE_RECORD,
            "note": "notified in the Gazette of India",
        },
        {
            "label": "Baluchistan war funds subscription",
            "dates": "1932",
            "source": SOURCE_RECORD,
        },
    ],
    "RULE": [
        {
            "label": "Indian Army Act, 1911, s. 4",
            "note": "cited as the basis for honorary rank",
        },
        {"label": "Treaty of Jacobabad, 1876"},
    ],
    "RELATION": [
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
    "AUTHOR ID": [
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
