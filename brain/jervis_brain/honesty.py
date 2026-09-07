"""Stop the model claiming an action it never performed.

PLAN.md §4 Phase 2 scenario 6. The model sometimes narrates a write ("Done, I've
deleted it") in a turn where no write ever ran - after a refusal, a decline, or a
tool error. Saying that aloud to someone who then walks away believing it is the
worst failure this system can have, so it is checked after the fact rather than only
asked for in the prompt.
"""

from __future__ import annotations

import re

# First person, past tense, about something in the world. Deliberately narrow: it must
# not fire on "I couldn't delete it" or "Shall I delete it?".
_CLAIM_VERBS = (
    "deleted",
    "removed",
    "trashed",
    "sent",
    "emailed",
    "texted",
    "messaged",
    "created",
    "wrote",
    "saved",
    "opened",
    "launched",
    "moved",
    "renamed",
    "copied",
    "ordered",
    "placed",
    "set",
    "changed",
    "updated",
    "installed",
    "cancelled",
    "canceled",
    "scheduled",
    "added",
    "posted",
    "shared",
)
_CLAIM = re.compile(
    r"(?:\b(?:I|I've|I have|I'ye)\s+(?:just\s+|now\s+)?(?:" + "|".join(_CLAIM_VERBS) + r")\b"
    r"|^(?:done|all done|that's done|sorted|there you go)\b)",
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"\b(?:could\s?n[o']t|cannot|can't|did\s?n[o']t|was\s?n[o']t|un(?:able|available)"
    r"|failed|refus|block|decline|would\s+have|will|shall|should\s+I)\b",
    re.IGNORECASE,
)

NOTE = "I should be clear: I did not actually do that - nothing was carried out."

_SENTENCE = re.compile(r"[^.!?]*[.!?]|[^.!?]+$")


def split_sentences(text: str) -> list[str]:
    return [s for s in (m.group(0) for m in _SENTENCE.finditer(text)) if s.strip()]


def claims_an_action(sentence: str) -> bool:
    """True if *sentence* asserts a completed action, negations excluded."""
    if _NEGATED.search(sentence):
        return False
    return bool(_CLAIM.search(sentence.strip()))


def enforce(reply: str, *, performed_an_action: bool) -> str:
    """Strip unfounded claims from *reply* and append an honest note.

    A no-op when a write actually ran, or when the reply claims nothing.
    """
    if performed_an_action or not reply.strip():
        return reply
    sentences = split_sentences(reply)
    kept = [s for s in sentences if not claims_an_action(s)]
    if len(kept) == len(sentences):
        return reply
    remainder = "".join(kept).strip()
    return f"{remainder} {NOTE}".strip() if remainder else NOTE
