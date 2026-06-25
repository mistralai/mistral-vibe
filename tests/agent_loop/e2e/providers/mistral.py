from __future__ import annotations

import httpx
import respx

from tests import constants as c
from tests.agent_loop.e2e.providers.base import ProviderAPI


class MistralAPI(ProviderAPI):
    base_url = c.MISTRAL_BASE_URL
    post_path = c.CHAT_COMPLETIONS_PATH

    def setup_router(self, router: respx.MockRouter) -> None:
        super().setup_router(router)
        router.get(c.CONNECTORS_BOOTSTRAP_PATH).mock(
            return_value=httpx.Response(200, json={"connectors": []})
        )
