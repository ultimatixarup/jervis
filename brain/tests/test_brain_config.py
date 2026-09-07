"""Config loading."""

from __future__ import annotations

from pathlib import Path

from jervis_brain.config import Config, load_config


def test_defaults_when_there_is_no_file(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.yaml", load_env=False)
    assert config.model == "claude-sonnet-5"
    assert config.max_tool_rounds == 12
    assert config.servers == ()
    assert config.permissions.confirm_window_seconds == 60


def test_reads_the_shipped_example() -> None:
    """config.example.yaml is what setup.sh installs, so it must actually parse."""
    config = load_config(Path(__file__).parents[2] / "config.example.yaml", load_env=False)
    assert config.model.startswith("claude-")
    assert {s.name for s in config.servers} == {"macos", "imessage", "mail", "bank", "ubereats"}
    # Only phases that have landed are enabled; see the comment in the example file.
    assert {s.name for s in config.enabled_servers} == {"macos"}
    assert config.voice.tts_backend == "say"
    assert config.http.port == 7777


def test_paths_are_expanded(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text(
        "paths:\n  home: ~/somewhere\n  audit_log: ~/somewhere/a.jsonl\n"
    )
    config = load_config(tmp_path / "c.yaml", load_env=False)
    assert config.paths.home == Path.home() / "somewhere"
    assert config.paths.audit_log == Path.home() / "somewhere/a.jsonl"
    # Unset paths default to sit under `home`.
    assert config.paths.memory_db == Path.home() / "somewhere/memory.db"


def test_server_modules_are_derived_from_names(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text(
        "servers:\n  macos: { enabled: true }\n  bank: { enabled: false, env: sandbox }\n"
    )
    config = load_config(tmp_path / "c.yaml", load_env=False)
    by_name = {s.name: s for s in config.servers}
    assert by_name["macos"].import_module == "jervis_mcp_macos"
    assert by_name["bank"].env == "sandbox"
    assert [s.name for s in config.enabled_servers] == ["macos"]


def test_extra_destructive_patterns(tmp_path: Path) -> None:
    # Single-quoted YAML keeps backslashes literal, which is what a regex needs.
    (tmp_path / "c.yaml").write_text(
        "permissions:\n"
        "  confirm_window_seconds: 30\n"
        "  extra_destructive_patterns: ['terraform\\s+destroy']\n"
    )
    config = load_config(tmp_path / "c.yaml", load_env=False)
    assert config.permissions.confirm_window_seconds == 30
    assert config.permissions.extra_destructive_patterns == (r"terraform\s+destroy",)


def test_an_empty_file_is_all_defaults(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text("")
    assert load_config(tmp_path / "c.yaml", load_env=False) == Config()
