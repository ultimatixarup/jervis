"""Repo-wide pytest configuration."""

from __future__ import annotations

import sys

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip `macos`-marked tests off macOS (PLAN.md §5)."""
    if sys.platform == "darwin":
        return
    skip = pytest.mark.skip(reason="needs live macOS services (Finder, Keychain, osascript)")
    for item in items:
        if "macos" in item.keywords:
            item.add_marker(skip)
