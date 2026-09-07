"""Phase 0 acceptance: the scaffold is coherent and the scripts are runnable.

These are deliberately about structure, not behaviour - there is no behaviour yet.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

WORKSPACE_MEMBERS = [
    "brain",
    "voice",
    "mcp/macos",
    "mcp/imessage",
    "mcp/mail",
    "mcp/bank",
    "mcp/ubereats",
]

SCRIPTS = [
    "_common.sh",
    "setup.sh",
    "doctor.sh",
    "grant-permissions.sh",
    "start.sh",
    "stop.sh",
    "test.sh",
    "test-e2e.sh",
    "test-voice.sh",
    "logs.sh",
]


def _root_toml() -> dict[str, object]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_workspace_lists_every_member() -> None:
    workspace = _root_toml()["tool"]["uv"]["workspace"]  # type: ignore[index]
    assert sorted(workspace["members"]) == sorted(WORKSPACE_MEMBERS)


def test_root_is_a_virtual_workspace() -> None:
    # No [project] table at the root, so `uv sync` syncs all members (CLAUDE.md).
    assert "project" not in _root_toml()


@pytest.mark.parametrize("member", WORKSPACE_MEMBERS)
def test_member_package_is_importable(member: str) -> None:
    with (REPO_ROOT / member / "pyproject.toml").open("rb") as fh:
        name = tomllib.load(fh)["project"]["name"]
    module = name.replace("-", "_")
    __import__(module)


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_is_executable_and_parses(script: str) -> None:
    path = REPO_ROOT / "scripts" / script
    assert path.is_file(), f"{script} is missing"
    assert os.access(path, os.X_OK), f"{script} is not executable"
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_config_example_has_the_keys_the_code_will_read() -> None:
    config = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text())
    for key in ("model", "max_tool_rounds", "persona_name", "voice", "servers", "permissions"):
        assert key in config, f"config.example.yaml is missing {key!r}"
    assert config["model"].startswith("claude-"), "model must be a real Claude model id"
    assert set(config["servers"]) == {"macos", "imessage", "mail", "bank", "ubereats"}
    assert config["permissions"]["confirm_window_seconds"] == 60


def test_env_example_names_every_secret_and_holds_no_values() -> None:
    lines = (REPO_ROOT / ".env.example").read_text().splitlines()
    assignments = dict(
        line.split("=", 1) for line in lines if line and not line.startswith("#") and "=" in line
    )
    assert "ANTHROPIC_API_KEY" in assignments
    for name, value in assignments.items():
        if name == "PLAID_ENV":
            assert value == "sandbox"
        else:
            assert value == "", f"{name} must be empty in the example file"


def test_gitignore_covers_secrets_and_local_state() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text()
    for pattern in (".env", "*.db", "chrome-profile/", "*.jsonl", "gmail-token.json"):
        assert pattern in ignored, f".gitignore is missing {pattern}"
