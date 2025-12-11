from __future__ import annotations

import json
import os
import sys

from pydantic import ValidationError

from tests import TESTS_ROOT
from tests.mock.utils import MOCK_DATA_ENV_VAR
from vibe.core.types import LLMChunk

if __name__ == "__main__":
    sys.path.insert(0, str(TESTS_ROOT.parent)) # Ensure vibe is importable

    # This entrypoint is now mainly for running the actual vibe.acp.entrypoint.main
    # in an environment where conftest.py has set up global mocks.
    # The previous mocking logic here was conflicting with conftest.py.
    
    # Check if we need to configure mock behavior for mistralai client.
    # This should now be handled by conftest.py which uses environment variables.

    from vibe.acp.entrypoint import main

    main()
