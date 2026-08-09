from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import subprocess

import pytest
from pydantic import BaseModel, ConfigDict, Field

from golden_graph.annotation_artifacts import (
    AnnotationArtifactError,
    CanonicalArtifactAuthority,
    load_canonical_artifact,
    preflight_canonical_artifact,
    preflight_private_canonical_artifact,
    publish_canonical_artifact,
    read_private_worksheet_bytes,
    write_new_private_worksheet,
)
from golden_graph.canonical_io import canonical_json_bytes


class _FixtureArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(ge=1)
    artifact_role: str
    value: str


class _PrivateFixtureArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = 1
    artifact_role: str = "private_annotation_io_test"
    exact_quote: str


def _artifact(value: str = "stable") -> _FixtureArtifact:
    return _FixtureArtifact(
        schema_version=1,
        artifact_role="annotation_io_test",
        value=value,
    )


def test_publish_and_load_issue_only_token_gated_byte_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    artifact = _artifact()

    digest = publish_canonical_artifact(
        path,
        artifact,
        allowed_root=tmp_path,
    )
    loaded = load_canonical_artifact(
        path,
        _FixtureArtifact,
        allowed_root=tmp_path,
    )

    assert digest == hashlib.sha256(canonical_json_bytes(artifact)).hexdigest()
    assert loaded.artifact == artifact
    assert loaded.artifact_sha256 == digest
    assert loaded.artifact_path == path.resolve()
    assert path.with_suffix(".sha256").read_text(encoding="utf-8") == (
        f"{digest}  fixture.json\n"
    )
    with pytest.raises(TypeError, match="strict loader"):
        CanonicalArtifactAuthority(
            artifact=artifact,
            artifact_path=path.resolve(),
            artifact_sha256=digest,
        )


def test_identical_publications_converge_and_conflicts_never_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    expected = canonical_json_bytes(_artifact())

    first = publish_canonical_artifact(path, _artifact(), allowed_root=tmp_path)
    second = publish_canonical_artifact(path, _artifact(), allowed_root=tmp_path)

    assert first == second
    with pytest.raises(AnnotationArtifactError, match="Conflicting immutable"):
        publish_canonical_artifact(
            path,
            _artifact("different"),
            allowed_root=tmp_path,
        )
    assert path.read_bytes() == expected


def test_preflight_detects_conflict_without_writing_any_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"
    path.write_bytes(canonical_json_bytes(_artifact("conflict")))

    with pytest.raises(AnnotationArtifactError, match="Conflicting immutable"):
        preflight_canonical_artifact(path, _artifact(), allowed_root=tmp_path)

    assert not path.with_suffix(".sha256").exists()


def test_concurrent_identical_publications_converge(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.json"

    with ThreadPoolExecutor(max_workers=8) as executor:
        digests = list(
            executor.map(
                lambda _index: publish_canonical_artifact(
                    path,
                    _artifact(),
                    allowed_root=tmp_path,
                ),
                range(32),
            )
        )

    assert len(set(digests)) == 1
    loaded = load_canonical_artifact(
        path,
        _FixtureArtifact,
        allowed_root=tmp_path,
    )
    assert loaded.artifact == _artifact()


def test_identical_retry_repairs_json_only_crash_remnant(tmp_path: Path) -> None:
    path = tmp_path / "fixture.json"
    artifact = _artifact()
    payload = canonical_json_bytes(artifact)
    path.write_bytes(payload)

    digest = publish_canonical_artifact(
        path,
        artifact,
        allowed_root=tmp_path,
    )

    assert path.with_suffix(".sha256").read_bytes() == (
        f"{digest}  fixture.json\n".encode("utf-8")
    )


def test_loader_rejects_hardlinked_or_mismatched_leaves(tmp_path: Path) -> None:
    path = tmp_path / "fixture.json"
    publish_canonical_artifact(path, _artifact(), allowed_root=tmp_path)
    hardlink = tmp_path / "fixture-hardlink.json"
    try:
        os.link(path, hardlink)
    except OSError as exc:  # pragma: no cover - unusual filesystem policy
        pytest.skip(f"Hard links unavailable: {exc}")

    with pytest.raises(AnnotationArtifactError, match="Invalid annotation"):
        load_canonical_artifact(
            path,
            _FixtureArtifact,
            allowed_root=tmp_path,
        )


def test_private_worksheet_is_ignored_mutable_input_not_sealed_authority(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (tmp_path / ".gitignore").write_text("backend/data/\n", encoding="utf-8")
    private_root = tmp_path / "backend/data/golden_graph/annotations"
    private_root.mkdir(parents=True)
    path = private_root / "concepts.private.json"

    digest = write_new_private_worksheet(
        path,
        _artifact(),
        repository_root=tmp_path,
    )

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert read_private_worksheet_bytes(
        path,
        repository_root=tmp_path,
    ) == canonical_json_bytes(_artifact())
    assert not path.with_suffix(".sha256").exists()
    with pytest.raises(AnnotationArtifactError, match="already exists"):
        write_new_private_worksheet(
            path,
            _artifact(),
            repository_root=tmp_path,
        )


def test_private_boundary_rejects_force_added_ignored_file(
    tmp_path: Path,
) -> None:
    _initialize_private_repository(tmp_path)
    private_root = tmp_path / "backend/data/golden_graph/annotations"
    private_root.mkdir(parents=True)
    path = private_root / "tracked.private.json"
    path.write_bytes(canonical_json_bytes(_artifact()))
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-f", "--", path.relative_to(tmp_path)],
        check=True,
        capture_output=True,
    )

    with pytest.raises(AnnotationArtifactError, match="ignored and untracked"):
        read_private_worksheet_bytes(path, repository_root=tmp_path)


def test_private_preflight_allows_private_schema_but_writes_nothing(
    tmp_path: Path,
) -> None:
    _initialize_private_repository(tmp_path)
    private_root = tmp_path / "backend/data/golden_graph/annotations"
    private_root.mkdir(parents=True)
    path = private_root / "pass-a.private.json"
    artifact = _PrivateFixtureArtifact(exact_quote="private Source fragment")

    digest = preflight_private_canonical_artifact(
        path,
        artifact,
        repository_root=tmp_path,
    )

    assert digest == hashlib.sha256(canonical_json_bytes(artifact)).hexdigest()
    assert not path.exists()
    assert not path.with_suffix(".sha256").exists()
    path.write_bytes(b"conflicting private bytes\n")
    with pytest.raises(AnnotationArtifactError, match="Conflicting immutable"):
        preflight_private_canonical_artifact(
            path,
            artifact,
            repository_root=tmp_path,
        )
    assert not path.with_suffix(".sha256").exists()


def test_private_boundary_ignores_hostile_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _initialize_private_repository(tmp_path)
    private_root = tmp_path / "backend/data/golden_graph/annotations"
    private_root.mkdir(parents=True)
    path = private_root / "safe.private.json"
    path.write_bytes(canonical_json_bytes(_artifact()))
    hostile = tmp_path / "hostile-git-dir"
    hostile.mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / ("git.cmd" if os.name == "nt" else "git")
    fake_git.write_text(
        "@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("LD_PRELOAD", str(tmp_path / "hostile-loader.so"))
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", str(tmp_path / "hostile.dylib"))
    monkeypatch.setenv("GIT_DIR", str(hostile))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "not-the-worktree"))

    assert read_private_worksheet_bytes(
        path,
        repository_root=tmp_path,
    ) == canonical_json_bytes(_artifact())


def test_private_boundary_requires_exact_git_worktree(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("backend/data/\n", encoding="utf-8")
    private_root = tmp_path / "backend/data/golden_graph/annotations"
    private_root.mkdir(parents=True)
    path = private_root / "unsafe.private.json"

    with pytest.raises(AnnotationArtifactError, match="valid Git worktree"):
        write_new_private_worksheet(
            path,
            _artifact(),
            repository_root=tmp_path,
        )


def test_public_writer_rejects_private_quote_fields(tmp_path: Path) -> None:
    with pytest.raises(AnnotationArtifactError, match="exact_quote"):
        publish_canonical_artifact(
            tmp_path / "leak.json",
            _PrivateFixtureArtifact(exact_quote="private Source fragment"),
            allowed_root=tmp_path,
        )
    assert not (tmp_path / "leak.json").exists()


def _initialize_private_repository(root: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(root)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (root / ".gitignore").write_text("backend/data/\n", encoding="utf-8")
