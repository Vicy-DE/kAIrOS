"""kAIrOS session log [R44] — what the user did this session, in memory only.

The API handlers call note_event(kind, text) for every user input (an app prompt, a
question, a search, an image/video/music prompt, a game, a browsed address, a settings
change). The self-painting wallpaper (server.h_wallpaper, R44) keeps a random selection
of it and lets the LLM weave the survivors into one scene. Nothing leaves the machine
and nothing is persisted: a reboot is a fresh session.

(Until 2026-10-07 this log also triggered the R27 boot-animation dream; R73 replaced
that animation with a static boot screen, the log stays for the wallpaper.)
"""

MAX_EVENTS = 40
_EVENTS = []          # [(kind, text)]


def note_event(kind: str, text: str):
    """Called by the API handlers — builds the session context."""
    text = (text or "")[:300]
    _EVENTS.append((kind, text))
    del _EVENTS[:-MAX_EVENTS]


def session_events() -> list:
    """All user input logged this session: [(kind, text), …]."""
    return list(_EVENTS)


def clear():
    del _EVENTS[:]
