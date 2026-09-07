"""The claim check. PLAN.md §4 Phase 2 scenario 6."""

from __future__ import annotations

import pytest

from jervis_brain.honesty import NOTE, claims_an_action, enforce, split_sentences

CLAIMS = [
    "I've deleted the file.",
    "I have sent the message.",
    "I deleted report.pdf for you.",
    "Done.",
    "Done. That's gone.",
    "I've texted Kat to say you're running late.",
    "I opened Safari.",
    "I've set the volume to 30%.",
    "All done.",
    "I've ordered your usual.",
]

NOT_CLAIMS = [
    "I couldn't delete the file - it's in a blocked location.",
    "I can't send that.",
    "Shall I delete it?",
    "I didn't send it.",
    "That would have deleted your keys, so I refused.",
    "The folder has three files in it.",
    "I'll send it once you confirm.",
    "It's 4pm.",
    "There's nothing waiting to be confirmed.",
    "I was unable to open that app.",
]


@pytest.mark.parametrize("sentence", CLAIMS)
def test_claims_are_detected(sentence: str) -> None:
    assert claims_an_action(sentence)


@pytest.mark.parametrize("sentence", NOT_CLAIMS)
def test_non_claims_are_left_alone(sentence: str) -> None:
    assert not claims_an_action(sentence)


def test_enforce_is_a_noop_when_an_action_really_ran() -> None:
    reply = "I've deleted the file."
    assert enforce(reply, performed_an_action=True) == reply


def test_enforce_strips_the_claim_and_explains() -> None:
    result = enforce("I've deleted the file.", performed_an_action=False)
    assert "deleted" not in result
    assert result == NOTE


def test_enforce_keeps_the_honest_half() -> None:
    result = enforce(
        "The folder had three files. I've deleted the largest one.", performed_an_action=False
    )
    assert "three files" in result
    assert "deleted" not in result
    assert NOTE in result


def test_enforce_leaves_a_reply_that_claims_nothing() -> None:
    reply = "There are two files in Downloads."
    assert enforce(reply, performed_an_action=False) == reply


def test_enforce_handles_an_empty_reply() -> None:
    assert enforce("", performed_an_action=False) == ""
    assert enforce("   ", performed_an_action=False) == "   "


def test_split_sentences() -> None:
    assert split_sentences("One. Two! Three?") == ["One.", " Two!", " Three?"]
    assert split_sentences("No trailing punctuation") == ["No trailing punctuation"]
    assert split_sentences("") == []
