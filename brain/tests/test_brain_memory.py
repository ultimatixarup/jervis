"""The SQLite memory store."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jervis_brain.memory import Kind, MemoryStore, summarise_memories


@pytest.fixture
def store(tmp_path: Path) -> Iterator[MemoryStore]:
    with MemoryStore(tmp_path / "memory.db") as store:
        yield store


def test_remember_and_recall(store: MemoryStore) -> None:
    store.remember(Kind.FACT, "Arup's dentist is Dr Chen on Wilshire")
    store.remember(Kind.PREFERENCE, "Prefers oat milk in coffee")
    hits = store.recall("dentist")
    assert len(hits) == 1
    assert "Dr Chen" in hits[0].text
    assert hits[0].kind is Kind.FACT


def test_remember_dedupes_identical_text(store: MemoryStore) -> None:
    first = store.remember(Kind.FACT, "The wifi password is hunter2")
    second = store.remember(Kind.FACT, "The wifi password is hunter2")
    assert first == second
    assert len(store.recall("wifi")) == 1


def test_remember_strips_whitespace_when_deduping(store: MemoryStore) -> None:
    assert store.remember(Kind.FACT, "same thing") == store.remember(Kind.FACT, "  same thing  ")


def test_the_same_text_under_two_kinds_is_two_memories(store: MemoryStore) -> None:
    a = store.remember(Kind.FACT, "gym on tuesdays")
    b = store.remember(Kind.TASK, "gym on tuesdays")
    assert a != b


def test_remember_refuses_empty_text(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="empty"):
        store.remember(Kind.FACT, "   ")


def test_recall_ranks_exact_matches_first(store: MemoryStore) -> None:
    store.remember(Kind.FACT, "The dentist appointment moved to Friday afternoon")
    store.remember(Kind.FACT, "dentist")
    store.remember(Kind.FACT, "Call the dentist about the dentist referral form")
    assert store.recall("dentist")[0].text == "dentist"


def test_recall_ranks_substring_matches_above_stemmed_ones(store: MemoryStore) -> None:
    store.remember(Kind.FACT, "Running shoes are in the hall cupboard")
    store.remember(Kind.FACT, "The oat milk brand is Minor Figures")
    top = store.recall("oat milk")[0]
    assert "oat milk" in top.text.lower()


def test_recall_honours_k(store: MemoryStore) -> None:
    for i in range(20):
        store.remember(Kind.FACT, f"note number {i} about coffee")
    assert len(store.recall("coffee", k=5)) == 5
    assert len(store.recall("coffee")) == 8  # default k


def test_recall_survives_punctuation_and_fts_operators(store: MemoryStore) -> None:
    """Speech arrives with quotes and hyphens; FTS5 would treat them as syntax."""
    store.remember(Kind.FACT, "The router is a Netgear Orbi")
    for query in ['"router"', "router-", "router OR", "router AND NOT", "what's the router?"]:
        assert store.recall(query), f"query {query!r} found nothing"


def test_recall_with_no_searchable_words(store: MemoryStore) -> None:
    store.remember(Kind.FACT, "something")
    assert store.recall("???") == []
    assert store.recall("") == []


def test_recall_finds_nothing_when_there_is_nothing(store: MemoryStore) -> None:
    assert store.recall("anything") == []


def test_recent_tasks_are_newest_first(store: MemoryStore) -> None:
    for text in ("book flights", "renew passport", "call the bank"):
        store.remember(Kind.TASK, text)
    recent = store.recent_tasks(2)
    assert [m.text for m in recent] == ["call the bank", "renew passport"]


def test_forget(store: MemoryStore) -> None:
    memory_id = store.remember(Kind.FACT, "temporary thing")
    assert store.forget(memory_id) is True
    assert store.recall("temporary") == []
    assert store.forget(memory_id) is False


def test_forgotten_memories_leave_the_search_index(store: MemoryStore) -> None:
    memory_id = store.remember(Kind.FACT, "indexed then removed")
    store.forget(memory_id)
    assert store.recall("indexed") == []


def test_plan_table_names_are_queryable(tmp_path: Path) -> None:
    """PLAN.md §2 names facts/preferences/tasks; they exist as views."""
    with MemoryStore(tmp_path / "m.db") as store:
        store.remember(Kind.FACT, "a fact")
        store.remember(Kind.PREFERENCE, "a preference")
        store.remember(Kind.TASK, "a task")
        cur = store._conn.cursor()
        for table, expected in (
            ("facts", "a fact"),
            ("preferences", "a preference"),
            ("tasks", "a task"),
        ):
            cur.execute(f"SELECT text FROM {table}")
            assert [r["text"] for r in cur.fetchall()] == [expected]


# --- conversation history -----------------------------------------------------------


def test_history_round_trip(store: MemoryStore) -> None:
    store.append_message("s1", "user", "hello")
    store.append_message("s1", "assistant", [{"type": "text", "text": "hi"}])
    store.append_message("s2", "user", "different session")
    assert store.history("s1") == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    assert store.history("s2") == [{"role": "user", "content": "different session"}]


def test_history_keeps_the_last_n_oldest_first(store: MemoryStore) -> None:
    for i in range(30):
        store.append_message("s1", "user", f"message {i}")
    window = store.history("s1", limit=20)
    assert len(window) == 20
    assert window[0]["content"] == "message 10"
    assert window[-1]["content"] == "message 29"


def test_history_drops_orphaned_tool_results_at_the_window_edge(store: MemoryStore) -> None:
    """A window that starts with a tool_result would be rejected by the API."""
    store.append_message("s1", "assistant", [{"type": "tool_use", "id": "t1", "name": "x"}])
    store.append_message("s1", "user", [{"type": "tool_result", "tool_use_id": "t1"}])
    store.append_message("s1", "assistant", [{"type": "text", "text": "done"}])
    window = store.history("s1", limit=2)
    assert window == [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}]


def test_replace_history(store: MemoryStore) -> None:
    store.append_message("s1", "user", "old")
    store.replace_history("s1", [{"role": "user", "content": "new"}])
    assert store.history("s1") == [{"role": "user", "content": "new"}]


def test_sessions(store: MemoryStore) -> None:
    store.append_message("b", "user", "x")
    store.append_message("a", "user", "x")
    assert store.sessions() == ["a", "b"]


def test_store_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    with MemoryStore(path) as store:
        store.remember(Kind.FACT, "durable fact")
    with MemoryStore(path) as store:
        assert store.recall("durable")[0].text == "durable fact"


def test_summarise_memories(store: MemoryStore) -> None:
    store.remember(Kind.PREFERENCE, "no sugar")
    assert summarise_memories(store.recall("sugar")) == "- (preference) no sugar"
