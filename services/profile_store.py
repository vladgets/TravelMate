"""Simple persistence for the free-text user travel profile."""

from pathlib import Path

_PROFILE_PATH = Path(__file__).parent.parent / "profiles" / "user_profile.txt"


def load_profile() -> str:
    if _PROFILE_PATH.exists():
        return _PROFILE_PATH.read_text().strip()
    return ""


def save_profile(text: str) -> None:
    _PROFILE_PATH.parent.mkdir(exist_ok=True)
    _PROFILE_PATH.write_text(text.strip())
