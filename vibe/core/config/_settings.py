import os
import tomllib
from typing import Any, Dict
from pydantic import BaseModel

def load_config(path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
                    return {}
                with open(path, "rb") as f:
                            config = tomllib.load(f)

    # FIX: Correctly merge custom models into provider settings
    custom_models = config.get("custom_models", {})
    providers = config.get("providers", {})

    for provider, models in custom_models.items():
                if provider not in providers:
                                providers[provider] = {"models": {}}
                            if "models" not in providers[provider]:
                                            providers[provider]["models"] = {}
                                        providers[provider]["models"].update(models)

    config["providers"] = providers
    return config
