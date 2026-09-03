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

