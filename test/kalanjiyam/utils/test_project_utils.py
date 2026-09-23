import pytest

import kalanjiyam.utils.project_utils as pu


@pytest.mark.parametrize(
    "n,expected",
    [
        (1, "i"),
        (2, "ii"),
        (3, "iii"),
        (4, "iv"),
        (5, "v"),
        (6, "vi"),
        (7, "vii"),
        (8, "viii"),
        (9, "ix"),
        (10, "x"),
        (11, "xi"),
        (14, "xiv"),
        (15, "xv"),
        (16, "xvi"),
        (19, "xix"),
        (20, "xx"),
        (21, "xxi"),
        (30, "xxx"),
        (40, "xl"),
        (50, "l"),
        (60, "lx"),
        (90, "xc"),
        (100, "c"),
    ],
)
def test_int_to_roman(n, expected):
    assert pu.int_to_roman(n) == expected


def test_parse_page_number_spec():
    assert pu.parse_page_number_spec("1 = -") == [pu.Rule(1, "-")]
    assert pu.parse_page_number_spec("3 = title") == [pu.Rule(3, "title")]


@pytest.mark.parametrize(
    "rules,expected",
    [
        ([], "1 2 3 4 5"),
        ([pu.Rule(1, "-")], "- - - - -"),
        ([pu.Rule(1, "2")], "2 3 4 5 6"),
        ([pu.Rule(2, "i")], "1 i ii iii iv"),
        ([pu.Rule(1, "i"), pu.Rule(3, "2")], "i ii 2 3 4"),
    ],
)
def test_apply_rules(rules, expected):
    assert pu.apply_rules(5, rules) == expected.split()


def test_parse_page_ranges():
    assert pu.parse_page_ranges("1, 3, 5-8") == [1, 3, 5, 6, 7, 8]
    assert pu.parse_page_ranges("5-2") == [2, 3, 4, 5]
    assert pu.parse_page_ranges("1; 2; 3") == [1, 2, 3]
    assert pu.parse_page_ranges("all", total_pages=4) == [1, 2, 3, 4]
    assert pu.parse_page_ranges("*", total_pages=3) == [1, 2, 3]
    assert pu.parse_page_ranges("") == []
    assert pu.parse_page_ranges("invalid, 5, abc, 8-10", total_pages=10) == [5, 8, 9, 10]


def test_normalize_condition_tags():
    raw = [
        {"name": "Shmushing", "pages": "1-3, 5"},
        {"name": "Torn", "pages": ""},
        "Blurry",
    ]
    normalized = pu.normalize_condition_tags(raw, total_pages=5)
    assert len(normalized) == 3
    assert normalized[0] == {
        "name": "Shmushing",
        "pages": "1-3, 5",
        "page_numbers": [1, 2, 3, 5],
    }
    assert normalized[1] == {
        "name": "Torn",
        "pages": "",
        "page_numbers": [],
    }
    assert normalized[2] == {
        "name": "Blurry",
        "pages": "",
        "page_numbers": [],
    }


def test_get_page_issues_map():
    tags = [
        {"name": "Shmushing", "pages": "1-2, 4", "page_numbers": [1, 2, 4]},
        {"name": "Torn", "pages": "", "page_numbers": []},
    ]
    issues_map = pu.get_page_issues_map(tags, total_pages=4)
    assert issues_map[1] == ["Shmushing", "Torn"]
    assert issues_map[2] == ["Shmushing", "Torn"]
    assert issues_map[3] == ["Torn"]
    assert issues_map[4] == ["Shmushing", "Torn"]


def test_normalize_folder_path():
    assert pu.normalize_folder_path(None) == ""
    assert pu.normalize_folder_path("") == ""
    assert pu.normalize_folder_path("   ") == ""
    assert pu.normalize_folder_path("/Philosophy/Nyaya/") == "Philosophy/Nyaya"
    assert pu.normalize_folder_path("Literature\\Poetry") == "Literature/Poetry"
    assert pu.normalize_folder_path("  Religion // Hindu /// Vedanta  ") == "Religion/Hindu/Vedanta"


def test_normalize_tags():
    assert pu.normalize_tags(None) == []
    assert pu.normalize_tags("") == []
    assert pu.normalize_tags("Sanskrit, Manuscript, Sanskrit, critical-edition") == [
        "Sanskrit",
        "Manuscript",
        "critical-edition",
    ]
    assert pu.normalize_tags(["Philosophy", "logic", "philosophy", "Nyaya"]) == [
        "Philosophy",
        "logic",
        "Nyaya",
    ]
    assert pu.normalize_tags('["Epic", "Mahabharata", "Epic"]') == [
        "Epic",
        "Mahabharata",
    ]


def test_get_folder_contents():
    class DummyProject:
        def __init__(self, id, title, folder="", tags=None):
            self.id = id
            self.title = title
            self.folder = folder
            self.folder_path = pu.normalize_folder_path(folder)
            self.tag_list = tags or []

    projects = [
        DummyProject(1, "Root Project", folder=""),
        DummyProject(2, "Nyaya Sutra", folder="Philosophy/Nyaya"),
        DummyProject(3, "Bhashya", folder="Philosophy/Nyaya"),
        DummyProject(4, "Mimamsa Sutra", folder="Philosophy/Mimamsa"),
        DummyProject(5, "Raghuvamsha", folder="Literature/Poetry/Kalidasa"),
    ]

    # Test root level folder contents
    root_contents = pu.get_folder_contents(projects, current_folder="")
    assert root_contents["current_folder"] == ""
    assert root_contents["parent_folder"] is None
    assert len(root_contents["breadcrumbs"]) == 1
    assert root_contents["breadcrumbs"][0] == {"name": "Root", "path": ""}
    assert len(root_contents["direct_projects"]) == 1
    assert root_contents["direct_projects"][0].title == "Root Project"
    # Subfolders at root should be Literature and Philosophy
    sub_names = [s["name"] for s in root_contents["subfolders"]]
    assert sub_names == ["Literature", "Philosophy"]
    sub_map = {s["name"]: s["count"] for s in root_contents["subfolders"]}
    assert sub_map["Literature"] == 1
    assert sub_map["Philosophy"] == 3

    # Test Philosophy folder level
    phil_contents = pu.get_folder_contents(projects, current_folder="Philosophy")
    assert phil_contents["current_folder"] == "Philosophy"
    assert phil_contents["parent_folder"] == ""
    assert [b["name"] for b in phil_contents["breadcrumbs"]] == ["Root", "Philosophy"]
    assert len(phil_contents["direct_projects"]) == 0
    phil_sub_names = [s["name"] for s in phil_contents["subfolders"]]
    assert phil_sub_names == ["Mimamsa", "Nyaya"]
    phil_sub_map = {s["name"]: s["count"] for s in phil_contents["subfolders"]}
    assert phil_sub_map["Mimamsa"] == 1
    assert phil_sub_map["Nyaya"] == 2

    # Test Philosophy/Nyaya folder level
    nyaya_contents = pu.get_folder_contents(projects, current_folder="Philosophy/Nyaya")
    assert nyaya_contents["current_folder"] == "Philosophy/Nyaya"
    assert nyaya_contents["parent_folder"] == "Philosophy"
    assert len(nyaya_contents["direct_projects"]) == 2
    assert len(nyaya_contents["subfolders"]) == 0
    assert nyaya_contents["total_projects_in_scope"] == 2


def test_get_folder_contents_with_empty_folders():
    class DummyProject:
        def __init__(self, id, title, folder=""):
            self.id = id
            self.title = title
            self.folder = folder
            self.folder_path = pu.normalize_folder_path(folder)

    projects = [
        DummyProject(1, "Project In Novels", folder="Novels/Drama"),
    ]
    all_known_folders = ["Novels", "Novels/Drama", "Novels/Poetry", "EmptyRootFolder"]

    root_contents = pu.get_folder_contents(
        projects, current_folder="", all_known_folders=all_known_folders
    )
    assert root_contents["current_folder"] == ""
    sub_names = [s["name"] for s in root_contents["subfolders"]]
    assert "EmptyRootFolder" in sub_names
    assert "Novels" in sub_names
    sub_map = {s["name"]: s["count"] for s in root_contents["subfolders"]}
    assert sub_map["EmptyRootFolder"] == 0
    assert sub_map["Novels"] == 1

    # Check inside Novels
    novels_contents = pu.get_folder_contents(
        projects, current_folder="Novels", all_known_folders=all_known_folders
    )
    sub_novels = {s["name"]: s["count"] for s in novels_contents["subfolders"]}
    assert "Poetry" in sub_novels
    assert sub_novels["Poetry"] == 0
    assert sub_novels["Drama"] == 1


def test_folder_organization_isolation(flask_app):
    """Test that ProofFolders are scoped by organization_id and can share names across orgs."""
    from kalanjiyam.queries import get_session

    with flask_app.app_context():
        session = get_session()

        # Org 101 creates "Science" and "Literature"
        f1 = pu.ensure_proof_folder(session, "Science", organization_id=101)
        f2 = pu.ensure_proof_folder(session, "Literature/Fiction", organization_id=101)
        session.commit()

        # Org 102 creates "Science" (same name) and "History"
        f3 = pu.ensure_proof_folder(session, "Science", organization_id=102)
        f4 = pu.ensure_proof_folder(session, "History", organization_id=102)
        session.commit()

        assert f1.id != f3.id
        assert f1.organization_id == 101
        assert f3.organization_id == 102

        # Query available folders for Org 101
        org1_folders = pu.get_all_available_folders(session, organization_id=101)
        assert "Science" in org1_folders
        assert "Literature" in org1_folders
        assert "Literature/Fiction" in org1_folders
        assert "History" not in org1_folders

        # Query available folders for Org 102
        org2_folders = pu.get_all_available_folders(session, organization_id=102)
        assert "Science" in org2_folders
        assert "History" in org2_folders
        assert "Literature" not in org2_folders
        assert "Literature/Fiction" not in org2_folders


