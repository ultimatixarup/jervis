"""Destructive-command detection and blocked-path enforcement."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jervis_mcp_macos.safety import (
    BlockedByPolicy,
    assert_command_allowed,
    assert_path_allowed,
    destructive_match,
    find_blocked_reference,
    is_blocked_path,
    is_destructive,
    normalise_path,
)

HOME = Path.home()

# --- commands that must escalate to `confirm` -------------------------------------
DESTRUCTIVE = [
    "rm file.txt",
    "rm -rf /tmp/build",
    "cd /tmp && rm -r build",
    "find . -name '*.o' | xargs rm",
    "RM Uppercase.txt",
    "sudo reboot",
    "sudo -u root ls /",
    "kill 1234",
    "kill -9 $(pgrep node)",
    "killall Finder",
    "echo boom > /dev/disk2",
    "cat x >  /dev/rdisk0",
    "mkfs.ext4 /dev/disk2",
    "diskutil eraseDisk JHFS+ Blank disk2",
    "chmod -R 777 .",
    "chmod -Rf 755 /tmp/x",
    "git push --force origin main",
    "git push -f",
    "git push origin main --force-with-lease",
    "git push --force-with-lease",
    "dd if=/dev/zero of=/dev/disk2 bs=1m",
    "dd if=a.img of=/Volumes/disk",
]

# --- commands that must NOT escalate ----------------------------------------------
BENIGN = [
    "rmdir empty_dir",
    "set an alarm for 8am",
    "python -c 'print(format(1.5))'",
    "diskutility --help",
    "git push origin main",
    "git pushall",
    "chmod 755 script.sh",
    "chmod -v 644 notes.txt",
    "chmodx -R .",
    "ls /Users/me/Documents",
    "grep -r TODO .",
    "make install",
    "uv run pytest -q",
    "echo 'skill issue'",
    "pseudo-random number",
    "warm up the cache",
    "check the firmware version",
    "npm run term",
    "ls -la ~/Desktop",
    "open -a Safari",
]


@pytest.mark.parametrize("cmd", DESTRUCTIVE)
def test_destructive_commands_are_flagged(cmd: str) -> None:
    assert is_destructive(cmd), f"{cmd!r} should require confirmation"


@pytest.mark.parametrize("cmd", BENIGN)
def test_benign_commands_are_not_flagged(cmd: str) -> None:
    match = destructive_match(cmd)
    assert match is None, f"{cmd!r} wrongly matched {match}"


def test_table_is_big_enough() -> None:
    # PLAN.md §4 Phase 1 asks for at least 30 cases across both directions.
    assert len(DESTRUCTIVE) + len(BENIGN) >= 30


@pytest.mark.parametrize(
    "cmd",
    [
        "echo done > /dev/null",  # harmless, but matches the plan's `>\s*/dev/`
        "say 'kill the lights'",  # the word, not the command
    ],
)
def test_known_over_triggers_still_flag(cmd: str) -> None:
    """Documented false positives.

    Both are text matches on a plan-mandated pattern. Over-triggering costs one spoken
    "yes"; under-triggering can cost data, so these stay as they are.
    """
    assert is_destructive(cmd)


def test_extra_patterns_are_honoured() -> None:
    assert not is_destructive("terraform destroy")
    assert is_destructive("terraform destroy", extra_patterns=[r"terraform\s+destroy"])


# --- blocked paths -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "~/.ssh",
        "~/.ssh/id_rsa",
        "$HOME/.ssh/config",
        "~/Library/Keychains",
        "~/Library/Keychains/login.keychain-db",
        "/System",
        "/System/Library/CoreServices",
        str(HOME / ".ssh" / "id_ed25519"),
        "~/Desktop/../.ssh/id_rsa",
        "~/./.ssh/./known_hosts",
    ],
)
def test_blocked_paths(path: str) -> None:
    assert is_blocked_path(path), f"{path} must be blocked"


@pytest.mark.parametrize(
    "path",
    [
        "~/Desktop",
        "~/Desktop/notes.txt",
        "/tmp",
        "/Users",
        str(HOME / "Downloads"),
        "~/.sshfs-cache",  # near-miss: a sibling of .ssh, not inside it
        "~/Library/Keychain-notes.txt",
        "/System/Volumes/Data" + str(HOME / "Desktop"),  # firmlinked = the real Desktop
    ],
)
def test_allowed_paths(path: str) -> None:
    assert not is_blocked_path(path), f"{path} must be allowed"


def test_relative_path_is_resolved_against_cwd() -> None:
    assert is_blocked_path(".ssh/id_rsa", cwd=HOME)
    assert not is_blocked_path("Desktop/notes.txt", cwd=HOME)


def test_symlink_into_a_blocked_root_is_blocked(tmp_path: Path) -> None:
    link = tmp_path / "innocent"
    os.symlink(HOME / ".ssh", link)
    assert is_blocked_path(link)
    assert is_blocked_path(link / "id_rsa")


def test_normalise_expands_home_and_traversal() -> None:
    assert normalise_path("~/Desktop/../Downloads") == Path(os.path.realpath(HOME / "Downloads"))


def test_normalise_undoes_the_data_firmlink() -> None:
    assert normalise_path("/System/Volumes/Data/Users") == Path("/Users")
    assert normalise_path("/System/Volumes/Data") == Path("/")


# --- scanning command text ---------------------------------------------------------


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("cat ~/.ssh/id_rsa", "~/.ssh/id_rsa"),
        ("ls -la /System/Library", "/System/Library"),
        (
            "security dump-keychain ~/Library/Keychains/login.keychain-db",
            "~/Library/Keychains/login.keychain-db",
        ),
        ('cp "$HOME/.ssh/config" /tmp/', "$HOME/.ssh/config"),
        ("echo hello", None),
        ("ls ~/Desktop", None),
        ("git status", None),
    ],
)
def test_find_blocked_reference(cmd: str, expected: str | None) -> None:
    assert find_blocked_reference(cmd) == expected


def test_assert_helpers_raise_with_a_usable_message() -> None:
    with pytest.raises(BlockedByPolicy, match="blocked by policy"):
        assert_path_allowed("~/.ssh/id_rsa")
    with pytest.raises(BlockedByPolicy, match="blocked by policy"):
        assert_command_allowed("cat ~/.ssh/id_rsa")

    # The allowed path comes back normalised, ready to use.
    assert assert_path_allowed("~/Desktop") == normalise_path("~/Desktop")
    assert_command_allowed("ls ~/Desktop")
