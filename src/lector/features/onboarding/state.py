"""Whether the first-run onboarding has been shown.

Milestone 4 asks only for a stub — placeholder screens, with the real flow
(mic permission request, activation mode selection) built in Milestone 8. So
this deliberately stores one boolean and nothing else: the choices the real
flow will collect already have homes in `features/settings/store.py`, and
inventing a schema for them now would be guessing at Milestone 8's design.
"""
from lector.features.settings import store as settings

_SEEN_KEY = "onboarding_seen"


def has_seen() -> bool:
    return bool(settings._load().get(_SEEN_KEY, False))


def mark_seen() -> None:
    data = settings._load()
    data[_SEEN_KEY] = True
    settings._save(data)
