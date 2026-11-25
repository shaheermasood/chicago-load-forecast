import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "find_comed_code.py"
spec = importlib.util.spec_from_file_location("find_comed_code", SCRIPT)
find_comed_code = importlib.util.module_from_spec(spec)
spec.loader.exec_module(find_comed_code)


def test_find_candidates_matches_by_name():
    facets = [
        {"id": "AAA", "name": "Some Other Utility"},
        {"id": "XYZ", "name": "Commonwealth Edison Company"},
        {"id": "QQQ", "name": "ComEd"},
    ]
    assert [f["id"] for f in find_comed_code.find_candidates(facets)] == ["XYZ", "QQQ"]


def test_find_candidates_none():
    assert find_comed_code.find_candidates([{"id": "AAA", "name": "Other"}]) == []
