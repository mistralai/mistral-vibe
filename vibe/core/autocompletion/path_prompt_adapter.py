from __future__ import annotations

from vibe.core.autocompletion.path_prompt import PathPromptPayload, PathResource


def extract_image_resources(payload: PathPromptPayload) -> list[PathResource]:
    return [r for r in payload.resources if r.kind == "image"]
