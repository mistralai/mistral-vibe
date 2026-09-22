"""Multi-provider completion adapters for the Local Runtime.

The subpackage keeps provider wire formats self-contained and translates them
to and from the Harness message model.

The public entry point is :func:`execute_generic_completion`, which the
completion dispatcher calls for every non-Mistral (``Backend.GENERIC``)
provider.
"""

from mistralai_vibe_local_harness.vibe.adapters.generic._execute import (
    execute_generic_completion,
)

__all__ = ["execute_generic_completion"]
