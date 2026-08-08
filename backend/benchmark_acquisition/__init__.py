"""Fail-closed acquisition for externally hosted benchmark materials.

This package is intentionally independent from ``app``.  It only verifies and
downloads registered bytes; product ingestion and evaluation happen behind
separate contracts.
"""

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

__all__ = [
    "AssetSpec",
    "CourseRegistration",
    "CorpusManifest",
    "CounterfactualFixture",
    "CounterfactualFixtureError",
    "ManifestError",
    "load_manifest",
    "load_counterfactual_fixture",
    "parse_manifest",
]
