"""The brain's safety module, and its relationship to the servers'.

The brain and each MCP server check independently (PLAN.md §4 Phase 1). Independent
implementations only help if neither can quietly become the weaker one, so the
cross-check below pins the direction: the brain must never flag *less*.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jervis_brain import safety as brain_safety
from jervis_mcp_macos import safety as macos_safety

# Every command the macOS server considers destructive, from its own test table.
SERVER_DESTRUCTIVE = [
    "rm file.txt",
    "rm -rf /tmp/build",
    "sudo reboot",
    "kill 1234",
    "killall Finder",
    "echo boom > /dev/disk2",
    "mkfs.ext4 /dev/disk2",
    "diskutil eraseDisk JHFS+ Blank disk2",
    "chmod -R 777 .",
    "chmod -Rf 755 /tmp/x",
    "git push --force origin main",
    "git push -f",
    "dd if=/dev/zero of=/dev/disk2 bs=1m",
]


@pytest.mark.parametrize("cmd", SERVER_DESTRUCTIVE)
def test_brain_flags_everything_the_server_flags(cmd: str) -> None:
    assert macos_safety.is_destructive(cmd), f"fixture is stale: {cmd!r}"
    assert brain_safety.is_destructive(cmd), (
        f"{cmd!r} escalates on the server but not in the brain - the brain must never "
        "be the weaker check"
    )


def test_brain_pattern_list_covers_the_servers() -> None:
    missing = set(macos_safety.DESTRUCTIVE_PATTERNS) - set(brain_safety.DESTRUCTIVE_PATTERNS)
    assert not missing, f"the macOS server has patterns the brain lacks: {missing}"


def test_brain_blocks_the_same_roots() -> None:
    assert set(macos_safety.BLOCKED_PATH_ROOTS) <= set(brain_safety.BLOCKED_PATH_ROOTS)


def test_the_two_modules_are_genuinely_independent() -> None:
    """The brain must not import its checks from a server package."""
    source = Path(brain_safety.__file__).read_text()
    assert "jervis_mcp_" not in source


@pytest.mark.parametrize(
    "path", ["~/.ssh/id_rsa", "~/Library/Keychains/login.keychain-db", "/System/Library"]
)
def test_blocked_paths(path: str) -> None:
    assert brain_safety.is_blocked_path(path)


@pytest.mark.parametrize("path", ["~/Desktop/a.txt", "/tmp", "~/.sshfs"])
def test_allowed_paths(path: str) -> None:
    assert not brain_safety.is_blocked_path(path)


def test_firmlink_and_traversal() -> None:
    assert brain_safety.normalise_path("/System/Volumes/Data/Users") == Path("/Users")
    assert brain_safety.is_blocked_path("~/Desktop/../.ssh/id_rsa")
    assert brain_safety.is_blocked_path(".ssh/id_rsa", cwd=Path.home())


def test_find_blocked_reference() -> None:
    assert brain_safety.find_blocked_reference("cat ~/.ssh/id_rsa") == "~/.ssh/id_rsa"
    assert brain_safety.find_blocked_reference("ls ~/Desktop") is None


def test_extra_patterns() -> None:
    assert brain_safety.destructive_match("hello") is None
    assert brain_safety.destructive_match("hello", [r"hello"]) == "hello"
