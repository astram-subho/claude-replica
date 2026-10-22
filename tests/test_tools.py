import pytest

from replica.agent import validate_input
from replica.tools import Toolbox, ToolError


@pytest.fixture
def box(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\nx = 3\n")
    return Toolbox(tmp_path)


def test_read_file_numbers_lines(box):
    assert box.run("read_file", {"path": "a.py", "offset": 2, "limit": 1}) == "     2\ty = 2"


def test_path_escape_rejected(box):
    with pytest.raises(ToolError, match="escapes"):
        box.run("read_file", {"path": "../etc/passwd"})


def test_edit_requires_unique_match(box):
    with pytest.raises(ToolError, match="occurs 2 times"):
        box.run("edit_file", {"path": "a.py", "old_string": "x =", "new_string": "z ="})
    box.run("edit_file", {"path": "a.py", "old_string": "x =", "new_string": "z =", "replace_all": True})
    assert "x =" not in (box.root / "a.py").read_text()


def test_write_glob_grep(box):
    box.run("write_file", {"path": "pkg/b.py", "contents": "def hello():\n    pass\n"})
    assert box.run("glob", {"pattern": "**/*.py"}).splitlines() == ["a.py", "pkg/b.py"]
    assert box.run("grep", {"pattern": r"def \w+"}) == "pkg/b.py:1:def hello():"


def test_bash_reports_exit_code(box):
    out = box.run("bash", {"command": "echo hi; exit 3"})
    assert out == "hi\n[exit code 3]"


def test_declined_mutation(tmp_path):
    box = Toolbox(tmp_path, approve=lambda name, args: False)
    with pytest.raises(ToolError, match="declined"):
        box.run("write_file", {"path": "f", "contents": ""})
    assert box.run("glob", {"pattern": "*"}) == "(no matches)"


def test_validate_input():
    assert validate_input("bash", {"command": "ls"}) is None
    assert validate_input("bash", {}) == "missing required field: command"
    assert validate_input("bash", {"command": "ls", "timeout": True}) == "field timeout should be integer"
    assert validate_input("read_file", {"path": "a", "bogus": 1}) == "unexpected field: bogus"
    assert validate_input("nope", {}) == "unknown tool: nope"
