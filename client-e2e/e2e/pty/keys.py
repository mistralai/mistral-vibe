"""Split raw terminal input into atomic keypresses."""

from __future__ import annotations

_FINAL_LO, _FINAL_HI = 0x40, 0x7E
_PASTE_START = "\x1b[200~"
_PASTE_END = "\x1b[201~"


def _is_final(character: str) -> bool:
    return _FINAL_LO <= ord(character) <= _FINAL_HI


def split_keys(text: str) -> list[str]:
    """Split characters and complete CSI, SS3, or Alt escape sequences."""
    keys: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith(_PASTE_START, index):
            end = text.find(_PASTE_END, index + len(_PASTE_START))
            if end >= 0:
                end += len(_PASTE_END)
                keys.append(text[index:end])
                index = end
                continue
        if text[index] != "\x1b":
            keys.append(text[index])
            index += 1
            continue
        end = index + 1
        if end < len(text) and text[end] in "[O":
            end += 1
            while end < len(text) and not _is_final(text[end]):
                end += 1
            if end < len(text):
                end += 1
        elif end < len(text):
            end += 1
        keys.append(text[index:end])
        index = end
    return keys
