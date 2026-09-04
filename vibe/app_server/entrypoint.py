from __future__ import annotations

import sys

if sys.argv[1:2] == ["--internal-posix-pty-helper"]:
    from mistralai_vibe_local_harness.vibe._processes._posix_helper import (  # pyright: ignore[reportMissingImports]
        main as _pty_helper_main,
    )

    raise SystemExit(_pty_helper_main(sys.argv[2:]))

from vibe.app_server.stdio import main

if __name__ == "__main__":
    main()
