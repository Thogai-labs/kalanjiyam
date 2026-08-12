import pytest

from kalanjiyam.views.proofing import main


@pytest.mark.parametrize(
    "path,expected",
    [
        ("book.pdf", True),
        ("book.djvu", False),
        ("book.epub", False),
    ],
)
def test_is_allowed_document_file(path, expected):
    assert main._is_allowed_document_file(path) == expected


def test_index(client):
    resp = client.get("/proofing/")
    assert resp.status_code == 200
    assert ">Proofing<" in resp.text


def test_index_pagination(client):
    resp = client.get("/proofing/?page=1&per_page=5")
    assert resp.status_code == 200
    assert ">Proofing<" in resp.text


def test_index_search(client):
    resp = client.get("/proofing/?q=test&sort=title&order=asc")
    assert resp.status_code == 200
    assert ">Proofing<" in resp.text


def test_beginners_guide(client):
    resp = client.get("/proofing/help/beginners-guide")
    assert "Beginner's Guide" in resp.text


def test_complete_guide(client):
    resp = client.get("/proofing/help/complete-guide")
    assert "Complete Proofing Guide" in resp.text


def test_editor_guide(client):
    resp = client.get("/proofing/help/editor-guide")
    assert "Editor Manual" in resp.text


def test_recent_changes(client):
    resp = client.get("/proofing/recent-changes")
    assert "Recent changes" in resp.text


def test_create_project__unauth(client):
    resp = client.get("/proofing/create-project")
    assert resp.status_code in (200, 302)


def test_create_project__auth(rama_client):
    resp = rama_client.get("/proofing/create-project")
    assert resp.status_code == 200


def test_talk(client):
    resp = client.get("/proofing/talk")
    assert "Talk" in resp.text


def test_help_index(client):
    resp = client.get("/proofing/help")
    assert resp.status_code == 200
    assert "How can we help you?" in resp.text
