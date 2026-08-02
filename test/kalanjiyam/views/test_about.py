def test_index(client):
    resp = client.get("/about/")
    assert "Mission" in resp.text


def test_mission(client):
    resp = client.get("/about/mission")
    assert "Our Mission" in resp.text


def test_values(client):
    resp = client.get("/about/values")
    assert "Values" in resp.text


def test_people(client):
    resp = client.get("/about/people", follow_redirects=True)
    assert "People" in resp.text


def test_people_core(client):
    resp = client.get("/about/people/core")
    assert "Our core team" in resp.text


def test_people_proofing(client):
    resp = client.get("/about/people/proofing")
    assert "Dozens of proofers" in resp.text


def test_code_and_data(client):
    resp = client.get("/about/code-and-data")
    assert "Code and Data" in resp.text


def test_name(client):
    resp = client.get("/about/our-name")
    assert "Kalanjiyam" in resp.text


def test_contact(client):
    resp = client.get("/about/contact")
    assert "Contact Us" in resp.text


def test_terms(client):
    resp = client.get("/about/terms")
    assert "Terms of Service" in resp.text


def test_privacy(client):
    resp = client.get("/about/privacy-policy")
    assert "Privacy Policy" in resp.text
