"""Where Claude Code may run, and what it may do."""

from __future__ import annotations

from pathlib import Path

import pytest

from jervis_mcp_claudecode.settings import (
    ALWAYS_DENIED,
    EDIT_TOOLS,
    READ_ONLY_TOOLS,
    Settings,
    is_blocked,
    list_projects,
    load_settings,
    resolve_project,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "code"
    for name in ("ideap", "jervis", ".hidden"):
        (root / name).mkdir(parents=True)
    (root / "notes.txt").write_text("not a project")
    return root


@pytest.fixture
def settings(workspace: Path) -> Settings:
    return Settings(projects=(workspace,))


def test_list_projects_skips_files_and_dotdirs(settings: Settings) -> None:
    assert [p.name for p in list_projects(settings)] == ["ideap", "jervis"]


def test_resolve_by_bare_name(settings: Settings, workspace: Path) -> None:
    assert resolve_project("ideap", settings) == workspace / "ideap"
    assert resolve_project("  IDEAP ", settings) == workspace / "ideap"


def test_resolve_by_path_inside_a_root(settings: Settings, workspace: Path) -> None:
    assert resolve_project(str(workspace / "jervis"), settings) == workspace / "jervis"


def test_an_unknown_name_lists_what_is_known(settings: Settings) -> None:
    with pytest.raises(ValueError, match="ideap, jervis"):
        resolve_project("nonsense", settings)


def test_a_path_outside_the_roots_is_refused(settings: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValueError, match="outside the directories"):
        resolve_project(str(outside), settings)


def test_traversal_out_of_a_root_is_refused(settings: Settings, workspace: Path) -> None:
    with pytest.raises(ValueError, match="outside the directories"):
        resolve_project(str(workspace / "ideap" / ".." / ".." / "elsewhere"), settings)


@pytest.mark.parametrize("path", ["~/.ssh", "~/Library/Keychains", "/System/Library"])
def test_blocked_roots_are_blocked(path: str) -> None:
    from jervis_mcp_claudecode.settings import _expand

    assert is_blocked(_expand(path))


def test_a_blocked_path_is_refused_even_if_a_root_contained_it(tmp_path: Path) -> None:
    settings = Settings(projects=(Path.home(),))
    with pytest.raises(ValueError, match="blocked by policy"):
        resolve_project("~/.ssh", settings)


def test_a_file_is_not_a_project(settings: Settings, workspace: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        resolve_project(str(workspace / "notes.txt"), settings)


def test_no_configured_projects_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="No project directories are configured"):
        resolve_project("anything", Settings())


# --- what Claude Code may do --------------------------------------------------------


def test_ask_gets_no_mutating_tools(settings: Settings) -> None:
    allowed = settings.allowed_tools(editing=False)
    assert allowed == READ_ONLY_TOOLS
    for forbidden in ("Edit", "Write", "Bash", "NotebookEdit"):
        assert forbidden not in allowed


def test_edit_tools_do_not_include_bash(settings: Settings) -> None:
    """Editing is allowed; running arbitrary commands is not, until opted in."""
    assert "Edit" in EDIT_TOOLS
    assert not any(t.startswith("Bash") for t in EDIT_TOOLS)


def test_extra_allowed_tools_widen_editing_only() -> None:
    settings = Settings(projects=(Path.home(),), extra_allowed_tools=("Bash(npm test:*)",))
    assert "Bash(npm test:*)" in settings.allowed_tools(editing=True)
    assert "Bash(npm test:*)" not in settings.allowed_tools(editing=False), (
        "a config allow-list must never loosen the read-only tool"
    )


def test_the_permanent_deny_list_covers_the_dangerous_ones() -> None:
    for pattern in ("Bash(rm:*)", "Bash(sudo:*)", "Bash(git push:*)"):
        assert pattern in ALWAYS_DENIED


# --- config loading -----------------------------------------------------------------


def test_defaults_when_the_config_has_no_claudecode_section(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text("model: claude-sonnet-5\n")
    settings = load_settings(tmp_path / "c.yaml")
    assert settings.projects == (Path.home() / "code",) or settings.projects[0].name == "code"
    assert settings.max_turns == 30
    assert settings.extra_allowed_tools == ()


def test_config_is_read(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text(
        "claudecode:\n"
        f"  projects: ['{tmp_path}']\n"
        "  model: claude-opus-5\n"
        "  max_turns: 5\n"
        "  task_timeout_seconds: 90\n"
        "  extra_allowed_tools: ['Bash(git status:*)']\n"
    )
    settings = load_settings(tmp_path / "c.yaml")
    assert settings.model == "claude-opus-5"
    assert settings.max_turns == 5
    assert settings.task_timeout_seconds == 90
    assert settings.extra_allowed_tools == ("Bash(git status:*)",)


def test_a_missing_config_file_is_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "absent.yaml").max_turns == 30


def test_an_empty_model_string_stays_empty(tmp_path: Path) -> None:
    """`model:` with no value is None in YAML; it must not become the string 'None'."""
    (tmp_path / "c.yaml").write_text('claudecode:\n  model:\n  projects: ["~/code"]\n')
    assert load_settings(tmp_path / "c.yaml").model == ""


def test_the_shipped_example_parses() -> None:
    example = Path(__file__).parents[3] / "config.example.yaml"
    settings = load_settings(example)
    assert settings.projects == (Path.home() / "code",)
    assert settings.model == ""
    assert settings.extra_allowed_tools == ()
