"""Versioned, fail-closed protocols for human golden-graph fixtures."""

from .canonical_io import (
    CanonicalArtifactError,
    canonical_json_bytes,
    load_hashed_canonical_json,
    write_draft_hashed_canonical_json,
)
from .protocol import (
    FrozenProtocolAuthority,
    GoldenGraphProtocolError,
    ManifestAuthority,
    freeze_protocol,
    load_frozen_protocol,
    load_manifest_authority,
    load_protocol,
    validate_protocol_for_freeze,
)
from .schemas import GoldenGraphProtocol

__all__ = [
    "CanonicalArtifactError",
    "GoldenGraphProtocol",
    "GoldenGraphProtocolError",
    "ManifestAuthority",
    "FrozenProtocolAuthority",
    "canonical_json_bytes",
    "freeze_protocol",
    "load_hashed_canonical_json",
    "load_frozen_protocol",
    "load_manifest_authority",
    "load_protocol",
    "validate_protocol_for_freeze",
    "write_draft_hashed_canonical_json",
]
