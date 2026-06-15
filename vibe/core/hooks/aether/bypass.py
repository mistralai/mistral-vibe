_GLOBAL_MARKERS = ("aether:skip", "suite:skip")


def is_bypassed(command: str, gate_markers: tuple[str, ...]) -> bool:
    all_markers = _GLOBAL_MARKERS + gate_markers
    return any(f"# {m}" in command for m in all_markers)
