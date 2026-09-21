import json
from dataclasses import dataclass


@dataclass
class Rule:
    start: int
    label: str


def int_to_roman(n: int) -> str:
    """Convert an integer to its roman numeral representation."""
    # Based on https://stackoverflow.com/questions/28777219
    roman = {
        1000: "m",
        500: "d",
        400: "cd",
        100: "c",
        90: "xc",
        50: "l",
        40: "xl",
        10: "x",
        9: "ix",
        5: "v",
        4: "iv",
        1: "i",
    }
    buf = []
    for r in roman.keys():
        x, y = divmod(n, r)
        buf.append(roman[r] * x)
        n -= r * x
        if n <= 0:
            break
    return "".join(buf)


def parse_page_number_spec(numbers: str) -> list[Rule]:
    """Parse the page number spec.

    This raises an exception if the spec is invalid.
    """
    rules = []
    for line in numbers.splitlines():
        start, _, label = line.partition("=")
        start = start.strip()
        label = label.strip()

        assert label
        assert start.isdigit()

        rules.append(Rule(start=int(start), label=label))

    rules = sorted(rules, key=lambda x: x.start)
    return rules


def apply_rules(num_pages: int, rules: list[Rule]):
    slugs = []

    for n in range(1, num_pages + 1):
        rule_matches = [r for r in rules if r.start <= n]
        if not rule_matches:
            slugs.append(str(n))
            continue

        # Get last matching rule, = highest precedence rule.
        rule = rule_matches[-1]
        if rule.label.isdigit():
            offset = n - rule.start
            slugs.append(str(int(rule.label) + offset))
        elif rule.label == "i":
            offset = n - rule.start
            slugs.append(int_to_roman(1 + offset))
        else:
            slugs.append(rule.label)

    return slugs


def parse_page_ranges(pages_str: str, total_pages: int | None = None) -> list[int]:
    """Parse a page range string into a list of sorted, unique 1-indexed page numbers.

    Examples:
        - "1, 3, 5-8" -> [1, 3, 5, 6, 7, 8]
        - "4-2" -> [2, 3, 4]
        - "all" or "*" -> list(range(1, total_pages + 1)) if total_pages else []
        - "" -> []
    """
    if not pages_str:
        return []

    pages_str = str(pages_str).strip().lower()
    if pages_str in ("all", "*"):
        return list(range(1, (total_pages or 0) + 1))

    parts = pages_str.replace(";", ",").split(",")
    nums = set()
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            left, right = bounds[0].strip(), bounds[1].strip()
            if left.isdigit() and right.isdigit():
                low = min(int(left), int(right))
                high = max(int(left), int(right))
                for p in range(low, high + 1):
                    if p > 0:
                        nums.add(p)
            elif left.isdigit():
                p = int(left)
                if p > 0:
                    nums.add(p)
        elif part.isdigit():
            p = int(part)
            if p > 0:
                nums.add(p)

    return sorted(nums)


def normalize_condition_tags(tags: list | str | None, total_pages: int | None = None) -> list[dict]:
    """Normalize raw condition tags into standard dictionary representations.

    Each item in the returned list has:
        - name: str (e.g. "Shmushing")
        - pages: str (e.g. "1-3, 5")
        - page_numbers: list[int] (e.g. [1, 2, 3, 5])
    """
    if not tags:
        return []

    if isinstance(tags, str):
        import json
        try:
            tags = json.loads(tags)
        except Exception:
            return []

    if not isinstance(tags, list):
        return []

    normalized = []
    for item in tags:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            pages_str = str(item.get("pages", "")).strip()
            page_nums = item.get("page_numbers")
            if not isinstance(page_nums, list) or not page_nums:
                page_nums = parse_page_ranges(pages_str, total_pages)
            normalized.append({
                "name": name,
                "pages": pages_str,
                "page_numbers": sorted(set(page_nums)),
            })
        elif isinstance(item, str):
            name = item.strip()
            if name:
                normalized.append({
                    "name": name,
                    "pages": "",
                    "page_numbers": [],
                })
    return normalized


def get_page_issues_map(condition_tags: list | str | None, total_pages: int = 0) -> dict[int, list[str]]:
    """Return a mapping of 1-indexed page numbers to list of issue tag names.

    If a tag has empty `page_numbers` and empty `pages`, it applies to all pages.
    """
    tags = normalize_condition_tags(condition_tags, total_pages=total_pages)
    mapping = {p: [] for p in range(1, total_pages + 1)}

    for tag in tags:
        name = tag["name"]
        page_nums = tag.get("page_numbers") or []
        pages_str = (tag.get("pages") or "").strip().lower()

        if not page_nums and (not pages_str or pages_str in ("all", "*")):
            for p in range(1, total_pages + 1):
                if name not in mapping[p]:
                    mapping[p].append(name)
        else:
            for p in page_nums:
                if p in mapping:
                    if name not in mapping[p]:
                        mapping[p].append(name)
                elif 1 <= p <= total_pages:
                    mapping.setdefault(p, []).append(name)

    return mapping


def normalize_folder_path(folder: str | None) -> str:
    """Normalize a folder path string.

    Converts backslashes to forward slashes, strips whitespace, collapses duplicate
    slashes, and removes leading/trailing slashes.
    Example: '  Philosophy / Nyaya / ' -> 'Philosophy/Nyaya'
    """
    if not folder:
        return ""
    parts = [p.strip() for p in str(folder).replace("\\", "/").split("/") if p.strip()]
    return "/".join(parts)


def normalize_tags(tags: list | str | None) -> list[str]:
    """Normalize project tags into a list of unique, non-empty stripped strings.

    Accepts a list of strings, a JSON string, or a comma-separated string.
    Example: 'Sanskrit, Manuscript, Sanskrit' -> ['Sanskrit', 'Manuscript']
    """
    if not tags:
        return []
    result = []
    if isinstance(tags, str):
        tags_str = tags.strip()
        if not tags_str:
            return []
        try:
            parsed = json.loads(tags_str)
            if isinstance(parsed, list):
                raw_list = parsed
            else:
                raw_list = [s.strip() for s in tags_str.split(",") if s.strip()]
        except Exception:
            raw_list = [s.strip() for s in tags_str.split(",") if s.strip()]
    elif isinstance(tags, (list, tuple, set)):
        raw_list = tags
    else:
        raw_list = []

    seen = set()
    for item in raw_list:
        clean = str(item).strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            result.append(clean)
    return result


def get_folder_contents(all_projects: list, current_folder: str = "") -> dict:
    """Calculate breadcrumbs, immediate subfolders with project counts, and direct projects for the given folder level.

    :param all_projects: list of Project models or objects with folder_path/folder attributes.
    :param current_folder: normalized folder path of the current level ('' for root).
    :return: dict with:
        - 'current_folder': normalized current path
        - 'breadcrumbs': list of dicts [{'name': '...', 'path': '...'}] from root to current_folder
        - 'parent_folder': path of parent level (None if at root)
        - 'subfolders': list of dicts [{'name': '...', 'path': '...', 'count': int}] sorted alphabetically
        - 'direct_projects': projects situated directly at current_folder
        - 'total_projects_in_scope': total projects in this folder and all subfolders
    """
    norm_current = normalize_folder_path(current_folder)

    breadcrumbs = [{"name": "All Folders", "path": ""}]
    if norm_current:
        segments = norm_current.split("/")
        for i in range(len(segments)):
            sub_path = "/".join(segments[: i + 1])
            breadcrumbs.append({"name": segments[i], "path": sub_path})

    parent_folder = None
    if norm_current:
        parent_parts = norm_current.split("/")[:-1]
        parent_folder = "/".join(parent_parts)

    subfolders_dict = {}
    direct_projects = []
    total_projects_in_scope = 0

    for project in all_projects:
        p_folder = getattr(project, "folder_path", None)
        if p_folder is None:
            p_folder = normalize_folder_path(getattr(project, "folder", ""))

        if norm_current == "":
            total_projects_in_scope += 1
            if not p_folder:
                direct_projects.append(project)
            else:
                top_seg = p_folder.split("/")[0]
                subfolders_dict[top_seg] = subfolders_dict.get(top_seg, 0) + 1
        else:
            if p_folder == norm_current:
                direct_projects.append(project)
                total_projects_in_scope += 1
            elif p_folder.startswith(norm_current + "/"):
                total_projects_in_scope += 1
                rel = p_folder[len(norm_current) + 1 :]
                sub_seg = rel.split("/")[0]
                subfolders_dict[sub_seg] = subfolders_dict.get(sub_seg, 0) + 1

    subfolders = [
        {
            "name": seg,
            "path": (f"{norm_current}/{seg}" if norm_current else seg),
            "count": count,
        }
        for seg, count in sorted(subfolders_dict.items(), key=lambda x: x[0].lower())
    ]

    return {
        "current_folder": norm_current,
        "breadcrumbs": breadcrumbs,
        "parent_folder": parent_folder,
        "subfolders": subfolders,
        "direct_projects": direct_projects,
        "total_projects_in_scope": total_projects_in_scope,
    }

