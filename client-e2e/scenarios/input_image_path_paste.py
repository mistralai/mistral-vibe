"""A pasted absolute image path becomes a quoted `@` mention."""

from __future__ import annotations

from pathlib import Path
import shutil

from e2e.app_server.config import E2E_DIR
from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

# The mention shows the pasted path verbatim, so its length must not depend on
# where the repo lives. A path derived from the repo root wraps at the 120-col
# width in a deep worktree checkout (but not a shallow clone), and
# `_normalize_vibe_dir` masks the prefix only after the wrap — so the golden
# would encode a checkout-dependent layout. Stage the fixture at a fixed,
# shallow path instead: both clients render `/tmp/...` verbatim (no symlink
# canonicalization), identically on macOS and Linux, short enough to never wrap.
# The file must exist or the paste stays literal text on Python instead of an
# `@` mention.
_SRC = E2E_DIR / "fixtures" / "pasted image.png"
_IMAGE = Path("/tmp/vibe_e2e_fixtures/pasted image.png")
_IMAGE.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(_SRC, _IMAGE)

timeline: Timeline = [paste(str(_IMAGE))]
