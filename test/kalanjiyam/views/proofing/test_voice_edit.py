"""Tests for the voice-edit endpoint and its client.

No test here reaches the network: `voice_client.transcribe_and_interpret` is
patched at every call site that would.
"""

import io
import json
from unittest.mock import patch

import pytest

from kalanjiyam.utils import voice_client
from kalanjiyam.utils.voice_client import (
    VoiceError,
    VoiceResult,
    _validate_clarification,
    _validate_command,
    _validate_ops,
    build_context,
)

URL = "/api/voice-edit/test-project/1/"

BLOCKS = [
    {"id": "block-a", "reading_order": 1, "content": "சித்த மருத்துவம் rama"},
    {"id": "block-b", "reading_order": 2, "content": "rama dasa"},
]


def _clip(data=b"fake-audio-bytes", name="utterance.webm", mime="audio/webm"):
    return (io.BytesIO(data), name, mime)


def _post(client, *, audio=None, context=None, language="ta"):
    return client.post(
        URL,
        data={
            "audio": audio if audio is not None else _clip(),
            "context": json.dumps(context if context is not None else {"blocks": BLOCKS}),
            "language": language,
        },
        content_type="multipart/form-data",
    )


@pytest.fixture()
def voice_on(flask_app):
    """Enable the feature for one test, then restore."""
    previous = flask_app.config.get("VOICE_EDIT_ENABLED")
    flask_app.config["VOICE_EDIT_ENABLED"] = True
    yield
    flask_app.config["VOICE_EDIT_ENABLED"] = previous


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------


def test_build_context_keeps_only_what_the_agents_need():
    context = build_context(
        [
            {
                "id": "block-a",
                "reading_order": 1,
                "content": "text",
                "language": "ta",
                "bbox": [1, 2, 3, 4],
                "confidence": 0.9,
                "words": [{"text": "text"}],
            }
        ]
    )
    assert context["blocks"] == [
        {"id": "block-a", "reading_order": 1, "content": "text", "language": "ta"}
    ]


def test_build_context_drops_blocks_without_an_id():
    context = build_context([{"content": "orphan"}, {"id": "block-a", "content": "kept"}])
    assert [b["id"] for b in context["blocks"]] == ["block-a"]


def test_build_context_truncates_long_blocks_and_long_pages():
    blocks = [{"id": f"b{i}", "content": "x" * 10_000} for i in range(300)]
    context = build_context(blocks)
    assert len(context["blocks"]) == voice_client.MAX_CONTEXT_BLOCKS
    assert len(context["blocks"][0]["content"]) == voice_client.MAX_BLOCK_CHARS


def test_build_context_includes_selection_and_pending_clarification():
    context = build_context(
        BLOCKS, selected_block_id="block-b", pending_clarification={"id": "c1"}
    )
    assert context["selected_block_id"] == "block-b"
    assert context["pending_clarification"] == {"id": "c1"}


def test_build_context_omits_optional_keys_when_absent():
    context = build_context(BLOCKS)
    assert "selected_block_id" not in context
    assert "pending_clarification" not in context


# ---------------------------------------------------------------------------
# Operation validation
# ---------------------------------------------------------------------------


def test_validate_ops_keeps_a_well_formed_replace():
    kept, dropped = _validate_ops(
        [{"op": "replace", "block_id": "block-a", "find": "rama", "replace": "rāma"}],
        {"block-a"},
    )
    assert dropped == []
    assert kept[0]["occurrence"] == 1


def test_validate_ops_drops_an_unknown_block():
    kept, dropped = _validate_ops(
        [{"op": "replace", "block_id": "ghost", "find": "a", "replace": "b"}], {"block-a"}
    )
    assert kept == []
    assert "not in context" in dropped[0][1]


def test_validate_ops_drops_an_unknown_op_type():
    kept, dropped = _validate_ops([{"op": "rewrite_page", "block_id": "block-a"}], {"block-a"})
    assert kept == []
    assert "unknown op" in dropped[0][1]


def test_validate_ops_drops_an_op_missing_required_fields():
    kept, dropped = _validate_ops([{"op": "replace", "block_id": "block-a"}], {"block-a"})
    assert kept == []
    assert "missing" in dropped[0][1]


def test_validate_ops_drops_an_op_with_no_block_id():
    kept, dropped = _validate_ops([{"op": "delete_block"}], {"block-a"})
    assert kept == []
    assert "missing block_id" in dropped[0][1]


def test_validate_ops_normalises_a_bad_occurrence():
    kept, _ = _validate_ops(
        [
            {
                "op": "replace",
                "block_id": "block-a",
                "find": "a",
                "replace": "b",
                "occurrence": "not-a-number",
            }
        ],
        {"block-a"},
    )
    assert kept[0]["occurrence"] == 1


def test_validate_ops_tolerates_a_non_list():
    assert _validate_ops("nonsense", {"block-a"}) == ([], [])


def test_validate_ops_allows_delete_block_with_no_extra_fields():
    kept, dropped = _validate_ops([{"op": "delete_block", "block_id": "block-a"}], {"block-a"})
    assert len(kept) == 1
    assert dropped == []


# ---------------------------------------------------------------------------
# Clarification and command validation
# ---------------------------------------------------------------------------


def test_validate_clarification_keeps_options_with_usable_ops():
    result = _validate_clarification(
        {
            "id": "c1",
            "question": "Which one?",
            "options": [
                {
                    "id": "a",
                    "label": "line 1",
                    "ops": [
                        {"op": "replace", "block_id": "block-a", "find": "x", "replace": "y"}
                    ],
                }
            ],
        },
        {"block-a"},
    )
    assert result["question"] == "Which one?"
    assert len(result["options"]) == 1


def test_validate_clarification_drops_options_whose_ops_are_all_invalid():
    # Offering a choice that would silently do nothing is worse than not
    # offering it at all.
    result = _validate_clarification(
        {
            "question": "Which one?",
            "options": [
                {"id": "a", "label": "line 1", "ops": [{"op": "replace", "block_id": "ghost"}]}
            ],
        },
        {"block-a"},
    )
    assert result is None


def test_validate_clarification_rejects_a_missing_question():
    assert _validate_clarification({"options": []}, {"block-a"}) is None
    assert _validate_clarification("nope", {"block-a"}) is None


def test_validate_command_accepts_known_actions():
    assert _validate_command({"action": "save"}) == {"action": "save", "args": {}}


def test_validate_command_rejects_unknown_actions():
    assert _validate_command({"action": "delete_the_project"}) is None
    assert _validate_command(None) is None


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------


def test_build_result_coerces_an_unknown_intent_to_noise():
    result = voice_client._build_result({"intent": "improvise"}, {"block-a"})
    assert result.intent == "noise"


def test_build_result_falls_back_to_noise_when_a_clarify_loses_its_card():
    result = voice_client._build_result(
        {"intent": "clarify", "clarification": {"question": ""}}, {"block-a"}
    )
    assert result.intent == "noise"


def test_result_ok_is_false_for_a_noise_turn():
    assert VoiceResult(intent="noise").ok is False
    assert VoiceResult(intent="question", answer="yes").ok is True


def test_to_api_dict_does_not_leak_dropped_ops():
    result = VoiceResult(transcript="hi", dropped=[({"op": "x"}, "unknown op")])
    assert "dropped" not in result.to_api_dict()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def test_endpoint_is_absent_when_the_feature_is_off(rama_client):
    assert _post(rama_client).status_code == 404


def test_endpoint_rejects_a_missing_audio_part(rama_client, voice_on):
    response = rama_client.post(
        URL,
        data={"context": json.dumps({"blocks": BLOCKS}), "language": "ta"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_endpoint_rejects_an_unsupported_audio_type(rama_client, voice_on):
    response = _post(rama_client, audio=_clip(name="clip.txt", mime="text/plain"))
    assert response.status_code == 400


def test_endpoint_rejects_an_empty_clip(rama_client, voice_on):
    assert _post(rama_client, audio=_clip(data=b"")).status_code == 400


def test_endpoint_rejects_an_oversized_clip(rama_client, voice_on):
    big = b"0" * (voice_client.MAX_AUDIO_BYTES + 1)
    assert _post(rama_client, audio=_clip(data=big)).status_code == 413


def test_endpoint_rejects_a_malformed_context(rama_client, voice_on):
    response = rama_client.post(
        URL,
        data={"audio": _clip(), "context": "{not json", "language": "ta"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_endpoint_returns_the_interpreted_turn(rama_client, voice_on):
    canned = VoiceResult(
        transcript="change rama to raama",
        language="ta",
        intent="edit",
        ops=[{"op": "replace", "block_id": "block-a", "find": "rama", "replace": "rāma"}],
    )
    with patch.object(voice_client, "transcribe_and_interpret", return_value=canned) as call:
        response = _post(rama_client)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["intent"] == "edit"
    assert payload["ops"][0]["replace"] == "rāma"
    assert payload["transcript"] == "change rama to raama"

    # The live client document is what gets forwarded, not anything from the DB.
    forwarded = call.call_args.kwargs["context"]
    assert [b["id"] for b in forwarded["blocks"]] == ["block-a", "block-b"]
    assert call.call_args.kwargs["language"] == "ta"


def test_endpoint_passes_a_noise_turn_through_untouched(rama_client, voice_on):
    with patch.object(
        voice_client, "transcribe_and_interpret", return_value=VoiceResult(intent="noise")
    ):
        response = _post(rama_client)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["intent"] == "noise"
    assert payload["ops"] == []


def test_endpoint_reports_an_upstream_failure_as_502(rama_client, voice_on):
    with patch.object(
        voice_client, "transcribe_and_interpret", side_effect=VoiceError("service down")
    ):
        response = _post(rama_client)
    # 502, not 500: the fault is upstream, and the editor keeps listening.
    assert response.status_code == 502


def test_endpoint_forwards_a_pending_clarification(rama_client, voice_on):
    with patch.object(
        voice_client, "transcribe_and_interpret", return_value=VoiceResult(intent="noise")
    ) as call:
        _post(
            rama_client,
            context={
                "blocks": BLOCKS,
                "selected_block_id": "block-b",
                "pending_clarification": {"id": "c1", "question": "Which one?"},
            },
        )

    forwarded = call.call_args.kwargs["context"]
    assert forwarded["pending_clarification"]["id"] == "c1"
    assert forwarded["selected_block_id"] == "block-b"


def test_endpoint_writes_no_revision(rama_client, voice_on, flask_app):
    """Voice edits land through the normal publish path, never from here."""
    import kalanjiyam.database as db
    from kalanjiyam.queries import get_session

    with flask_app.app_context():
        before = get_session().query(db.Revision).count()

    canned = VoiceResult(
        intent="edit",
        ops=[{"op": "replace", "block_id": "block-a", "find": "rama", "replace": "rāma"}],
    )
    with patch.object(voice_client, "transcribe_and_interpret", return_value=canned):
        assert _post(rama_client).status_code == 200

    with flask_app.app_context():
        assert get_session().query(db.Revision).count() == before


def test_endpoint_404s_for_an_unknown_project(rama_client, voice_on):
    response = rama_client.post(
        "/api/voice-edit/no-such-project/1/",
        data={"audio": _clip(), "context": json.dumps({"blocks": []}), "language": "ta"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 404
