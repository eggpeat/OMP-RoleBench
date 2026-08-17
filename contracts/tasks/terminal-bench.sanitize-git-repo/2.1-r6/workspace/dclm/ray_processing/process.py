"""Synthetic data-processing fixture containing fake credentials."""

from __future__ import annotations

import os


def configure_example_credentials() -> dict[str, str]:
    """Populate intentionally fake values for the sanitization exercise."""

    os.environ["AWS_ACCESS_KEY_ID"] = "<your-aws-access-key-id>"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "<your-aws-secret-access-key>"
    return {
        "status": "synthetic-fixture",
        "safe_value": "preserve-this-value",
    }
