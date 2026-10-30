from __future__ import annotations

import argparse
import asyncio
import json
import sys

from vibe.setup.auth._oidc_config import OidcError, require_oidc_enabled


async def _run(command: str) -> None:
    require_oidc_enabled()
    from vibe.setup.auth._oidc_service import OidcService

    service = OidcService.from_env()
    if command == "clear":
        await service.clear()
        print("Experimental OIDC credentials cleared; API keys unchanged.")
    else:
        print(json.dumps(await service.probe()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated CLI OIDC development probe")
    parser.add_argument("command", choices=("probe", "clear"))
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.command))
    except OidcError as exc:
        sys.exit(str(exc))
    except Exception:
        # Do not expose OAuth/library/keyring error payloads in the terminal.
        sys.exit("OIDC probe failed; retry later or explicitly re-login")


if __name__ == "__main__":
    main()
