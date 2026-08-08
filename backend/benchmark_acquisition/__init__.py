"""Fail-closed acquisition for externally hosted benchmark materials.

This package is intentionally independent from ``app``.  It only verifies and
downloads registered bytes; product ingestion and evaluation happen behind
separate contracts.
"""

from typing import TYPE_CHECKING, Any

from .manifest import (
    AssetSpec,
    CourseRegistration,
    CorpusManifest,
    ManifestError,
    load_manifest,
    parse_manifest,
)
from .counterfactual_fixture import (
    CounterfactualFixture,
    CounterfactualFixtureError,
    load_counterfactual_fixture,
)

if TYPE_CHECKING:
    from .fetch import (
        AcquisitionError,
        AcquisitionResult,
        VerifiedAssetReceipt,
        acquire_manifest,
        verify_registered_asset,
    )


_FETCH_EXPORTS = frozenset(
    {
        "AcquisitionError",
        "AcquisitionResult",
        "VerifiedAssetReceipt",
        "acquire_manifest",
        "verify_registered_asset",
    }
)


def __getattr__(name: str) -> Any:
    """Load fetch exports lazily so ``python -m ...fetch`` stays warning-free."""

    if name not in _FETCH_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import fetch

    value = getattr(fetch, name)
    globals()[name] = value
    return value

__all__ = [
    "AssetSpec",
    "AcquisitionError",
    "AcquisitionResult",
    "CourseRegistration",
    "CorpusManifest",
    "CounterfactualFixture",
    "CounterfactualFixtureError",
    "ManifestError",
    "VerifiedAssetReceipt",
    "acquire_manifest",
    "load_manifest",
    "load_counterfactual_fixture",
    "parse_manifest",
    "verify_registered_asset",
]
