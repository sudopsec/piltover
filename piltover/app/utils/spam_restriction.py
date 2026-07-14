from __future__ import annotations

SPAM_RESTRICTION_TEXT = (
    "Your account is limited. You can't send messages to users who aren't your contacts."
)


def spam_restriction_reasons() -> list:
    from piltover.tl import types

    return [
        types.RestrictionReason(
            platform="all",
            reason="spam",
            text=SPAM_RESTRICTION_TEXT,
        ),
    ]