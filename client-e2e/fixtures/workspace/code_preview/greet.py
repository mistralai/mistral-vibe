from __future__ import annotations

import sys

DEFAULT_NAME = "world"


def greeting(name: str) -> str:
    return f"Hello, {name}!"


def main() -> None:
    names = sys.argv[1:] or [DEFAULT_NAME]
    for name in names:
        print(greeting(name))


def shout(name: str) -> str:
    return greeting(name).upper()


def whisper(name: str) -> str:
    return greeting(name).lower()


def farewell(name: str) -> str:
    return f"Goodbye, {name}."


if __name__ == "__main__":
    main()
