"""
SecretsLoader — reads from .env locally.
Cert/prod: switches to AWS Secrets Manager automatically via ENV variable.
"""

import os
from dotenv import load_dotenv

load_dotenv()

_ENV = os.getenv("ENV", "local")


def get(key: str, default: str = None) -> str:
    """
    Get a secret by key.
    - local: reads from .env
    - cert/prod: reads from AWS Secrets Manager (wired up post-POC)
    """
    if _ENV == "local":
        value = os.getenv(key, default)
        if value is None:
            raise EnvironmentError(
                f"Missing required secret: '{key}'. "
                f"Add it to your .env file (see .env.example)."
            )
        return value

    # cert / prod — AWS Secrets Manager (post-POC)
    raise NotImplementedError(
        f"AWS Secrets Manager not wired for env='{_ENV}' yet. "
        "This is a post-POC task."
    )


def get_optional(key: str, default: str = None) -> str:
    """Get a secret that may not be set (returns default if missing)."""
    return os.getenv(key, default)
