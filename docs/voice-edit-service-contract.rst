Voice Edit Service Contract (v1.0)
==================================

This document is the authoritative contract between the Kalanjiyam proofing
editor and the **Yoajaka AI Service** voice-editing agents.  Hand this to the
agent team.  Any implementation that honours this shape plugs into the editor
with no frontend changes.

The editor sends one short audio clip plus the current page's text blocks, and
expects back a *decision*: what the user said, what they meant, and — when they
meant to change the page — a list of precise, verifiable edit operations.

Kalanjiyam performs no speech recognition and no language understanding.  All of
it lives behind this endpoint.

----

Endpoint
--------

``POST {OCR_SERVICE_URL}/v1/voice-edit``

Authentication is the header ``X-API-Key``, using the same host and key as
``/v1/ocr`` and ``/v1/chat``.  There is no separate voice service and no extra
secret.

The editor's client timeout is **30 seconds** with **no retries**.  This is
deliberately much shorter than the 300s allowed for OCR and chat: voice editing
is an interactive loop, the user is mid-sentence, and a slow answer is a useless
answer.  Budget accordingly — a p95 under 5 seconds is what makes the feature
feel handsfree.

----

Request
-------

``multipart/form-data`` with three parts:

``audio``
    The clip.  ``audio/webm;codecs=opus`` from Chrome and Firefox,
    ``audio/mp4`` from Safari.  Typically 2–15 seconds, hard-capped at 5 MB.
    Segmented client-side by silence detection, so it normally contains exactly
    one utterance.

``language``
    The language the user selected in the editor: ``ta``, ``sa``, ``hi``,
    ``en``, and the other codes in ``kalanjiyam/consts.py``.  Treat this as a
    strong hint for both recognition and output script, not as a hard
    constraint — users mix English command words into Tamil dictation
    constantly ("delete this line" spoken mid-Tamil-passage).

``context``
    A JSON string describing the page as the user currently sees it:

    .. code-block:: json

        {
          "blocks": [
            {"id": "block-x7", "reading_order": 2, "content": "…", "language": "ta"}
          ],
          "selected_block_id": "block-x7",
          "pending_clarification": {"id": "c1", "question": "…", "options": []}
        }

    Only ``id``, ``reading_order``, ``content``, and ``language`` are sent.
    Bounding boxes and confidences are deliberately withheld: they would bloat
    the payload and they are not evidence for any decision made here.

    ``blocks`` is the **live client document**, including edits the user has not
    yet published.  It is the only correct target for operations — never resolve
    a reference against any stored copy of the page.

    ``reading_order`` is what makes ordinal references resolvable.  "the second
    line" means the block with ``reading_order`` 2, not the second element of
    the array.

    ``selected_block_id`` is the block the cursor is in, when there is one.  It
    is the natural referent for "this line", "here", and "that word".

    ``pending_clarification`` is present only when the previous turn returned a
    clarification and the editor is waiting for an answer.  See
    `Clarification`_.

----

Response
--------

``Content-Type: application/json``.  Always JSON — never plain text, never a
bare string.

.. code-block:: json

    {
      "contract_version": "1.0",
      "transcript": "change rama to raama in the second line",
      "language": "ta",
      "intent": "edit",
      "ops": [
        {
          "op": "replace",
          "block_id": "block-x7",
          "find": "rama",
          "replace": "rāma",
          "occurrence": 1,
          "confidence": 0.93
        }
      ],
      "command": null,
      "answer": null,
      "clarification": null,
      "model": "yoajaka-voice-1",
      "usage": {}
    }

``transcript`` (required)
    What the user said, verbatim, in the script of the spoken language.  Always
    populate this even when no operation results — it is displayed in the editor
    so the user can see what was heard, and it is the first thing anyone looks at
    when a turn goes wrong.

``intent`` (required)
    Exactly one of:

    ``edit``
        A correction to existing text.  Populate ``ops``.
    ``dictate``
        New content to add.  Populate ``ops`` with ``append`` or ``insert_*``.
    ``navigate``
        A control action.  Populate ``command``.
    ``question``
        A question about the page that changes nothing.  Populate ``answer``.
    ``clarify``
        The request was understood but its target is ambiguous.  Populate
        ``clarification``, leave ``ops`` empty.
    ``noise``
        Nothing actionable was said.  Everything else empty.

``ops`` (required, may be empty)
    Edit operations, applied in array order.  See `Operations`_.

``command`` (nullable)
    ``{"action": "save", "args": {}}``.  See `Commands`_.

``answer`` (nullable)
    Plain text reply for ``intent: "question"``.  Shown in the panel, never
    inserted into the page.

``clarification`` (nullable)
    See `Clarification`_.

``model``, ``usage`` (optional)
    Provenance and token accounting, recorded in metrics.

----

Operations
----------

The vocabulary is small and closed on purpose.  The editor rejects any ``op``
it does not recognise, so an invented operation type is silently lost work.

Every operation targets a block by its ``id``.  There are no character offsets
anywhere in this contract — offsets go stale the moment the user types, and a
model cannot count UTF-8 code points in Tamil reliably enough to be trusted with
someone's manuscript.

.. list-table::
   :header-rows: 1
   :widths: 20 35 45

   * - ``op``
     - Fields
     - Meaning
   * - ``replace``
     - ``block_id``, ``find``, ``replace``, ``occurrence``
     - Replace a substring.  The common case.
   * - ``replace_block``
     - ``block_id``, ``content``
     - Rewrite a block wholesale.
   * - ``append``
     - ``block_id``, ``content``
     - Add text to the end of a block.  Dictation.
   * - ``insert_after``
     - ``block_id``, ``content``
     - New block immediately after this one.
   * - ``insert_before``
     - ``block_id``, ``content``
     - New block immediately before this one.
   * - ``delete_block``
     - ``block_id``
     - Remove a block entirely.
   * - ``set_language``
     - ``block_id``, ``language``
     - Tag a block's language.
   * - ``confidence``
     - float 0–1, on every op
     - How sure you are.  See `Confidence`_.

``occurrence`` is 1-based and defaults to 1.  Use it when ``find`` appears more
than once in the same block and the user's phrasing picks one out ("the second
rama").  If the phrasing does *not* pick one out, that is an ambiguity — return
a clarification instead of guessing.

Prefer ``replace`` over ``replace_block``.  A targeted substring edit shows the
user exactly what changed; a whole-block rewrite forces them to re-read the
block to find out.  Reach for ``replace_block`` only when the user genuinely
asked to redo the whole line.

----

Rules
-----

These five rules are what make the feature safe enough to leave the microphone
on.  They are not style suggestions — the editor enforces the first three and
will drop your output on the floor if you violate them.

**1. ``find`` must match exactly.**
The value of ``find`` must occur in that block's ``content`` verbatim,
character for character, including diacritics, combining marks, and whitespace.
The editor verifies this before applying and rejects the operation if it fails.
Do not normalise, do not transliterate, do not tidy up spacing — copy the exact
substring out of the ``content`` you were given.

**2. Ambiguity means clarify, never guess.**
If the phrase occurs in several blocks, or the instruction does not identify a
unique target, return ``intent: "clarify"`` with ``ops: []``.  A wrong edit that
looks confident is far more expensive than a question: the user is speaking, not
reading, and may not notice a silent mistake for several pages.

**3. Only touch blocks you were given.**
Never emit an operation for a ``block_id`` absent from ``context.blocks``.  The
editor strips these, but they indicate the request was misunderstood.

**4. Silence and noise are a clean no-op.**
An always-on microphone in a reading room will send you page rustle, throat
clearing, background conversation, and half-words.  Return
``intent: "noise"`` with empty ``ops``.  This is the single most frequent
outcome in real use.  It is normal, it is not an error, and it must never
produce an edit or an HTTP error status.

**5. Preserve the script.**
Never transliterate between scripts unless explicitly asked.  A Tamil block
stays in Tamil.  If the user dictates a word in English inside a Tamil passage,
keep it in Latin script — that is what they said.

----

Confidence
----------

Every operation carries a ``confidence`` in ``[0, 1]`` covering the whole
decision: that you heard the words correctly *and* that you identified the right
target.  A crisp transcription aimed at the wrong block is a low-confidence
operation, not a high-confidence one.

Below roughly 0.7, prefer a clarification over an operation.  The editor
surfaces confidence in its review list, but the decision to ask rather than act
belongs to you — the editor cannot tell a shaky edit from a certain one.

----

Clarification
-------------

.. code-block:: json

    {
      "intent": "clarify",
      "ops": [],
      "clarification": {
        "id": "c1",
        "question": "Which 'rama' did you mean?",
        "options": [
          {"id": "a", "label": "line 2 — rama dāsa", "ops": [{"op": "replace", "…": "…"}]},
          {"id": "b", "label": "line 7 — śrī rama", "ops": [{"op": "replace", "…": "…"}]}
        ]
      }
    }

Each option carries the **complete operations** that would apply if chosen.  The
editor can then resolve a click instantly with no second round trip.

Keep ``question`` to one short sentence and ``label`` to a phrase that names the
location and shows enough surrounding text to tell the options apart.  The user
is listening and glancing, not reading carefully.  Two to four options; if you
have more than four candidates the request was too vague to disambiguate this
way — ask a narrowing question instead.

When the next turn arrives with ``pending_clarification`` set, interpret the
audio as an answer to that question.  Users answer positionally ("the second
one", "the first"), by content ("the one in line seven"), or abandon it
entirely ("no, forget it" → ``intent: "noise"``, and the editor drops the
pending clarification).

----

Commands
--------

For ``intent: "navigate"``.  Also a closed vocabulary:

``save``, ``next_page``, ``prev_page``, ``undo``, ``stop_listening``,
``zoom_in``, ``zoom_out``, ``reset_zoom``, ``select_block``.

``select_block`` takes ``args: {"block_id": "…"}``.  The rest take no arguments.

``undo`` reverses the previous voice turn — it is the spoken form of "no, not
that".  Expect it to be used immediately after a bad edit, and expect the user
to say it with some irritation.  Treat any clear reversal phrasing as ``undo``.

----

Errors
------

Return a normal JSON body with ``intent: "noise"`` for anything the user did
wrong — inaudible audio, an empty clip, an unintelligible request.  These are
not exceptional; they are the steady state of an open microphone.

Reserve non-2xx status codes for genuine service faults: authentication
failures, an unavailable model, a malformed request.  Use the same error shape
as the other endpoints on this host::

    {"detail": {"code": "…", "message": "…"}}

The editor treats a 5xx as a transient failure, shows a quiet status, and keeps
listening.  It does not retry — by the time a retry completed, the user would
have said something else.

----

Suggested agent decomposition
-----------------------------

Not binding, but this is the shape the contract was designed around:

1. **ASR** — audio + language hint → transcript.  Bail to ``noise`` here on
   silence or an unintelligible clip; this is the cheapest place to reject, and
   most turns end here.
2. **Intent classifier** — transcript + context → one of the six intents.  Small
   and fast; it gates everything downstream.
3. **Block targeter** (``edit`` and ``dictate`` only) — transcript + blocks →
   operations with confidence, escalating to a clarification below threshold.
   This is where the real work is, and the only stage that needs the full block
   list.
4. **Answerer** (``question`` only) — transcript + blocks → plain text.

Personas live in ``personas.yaml`` alongside the existing ``kalanjiyam-*``
entries.

----

Testing
-------

Kalanjiyam's own tests never reach the network.  To exercise the client path
before the agents exist, point ``OCR_SERVICE_URL`` at a stub serving canned
responses and cover, at minimum:

- a clean single ``replace``
- a ``find`` that does not match the block — must be rejected visibly, and the
  block must be left untouched
- a clarification, answered on the following turn
- ``intent: "noise"`` — must be a completely silent no-op
- an operation naming a ``block_id`` not in the context — must be stripped
