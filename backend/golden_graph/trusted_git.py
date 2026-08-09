"""Trusted Git executable and minimal-environment resolution.

Repository history is an authority boundary for golden-graph artifacts.  A
bare ``git`` command or inherited loader environment would let the caller
replace that authority with an arbitrary executable.  This module resolves a
machine-installed Git from fixed/OS-owned locations and constructs the small
environment required by read-only plumbing commands.
"""

from __future__ import annotations

import os
from pathlib import Path


_TRUSTED_UNIX_GIT_PATHS = (
    Path("/usr/bin/git"),
    Path("/bin/git"),
    Path("/usr/local/bin/git"),
)


class TrustedGitError(ValueError):
    """Raised when a trusted Git runtime cannot be established."""


def resolve_trusted_git_executable() -> str:
    """Return an absolute regular Git executable without consulting PATH."""

    candidates = (
        _trusted_windows_git_candidates()
        if os.name == "nt"
        else _TRUSTED_UNIX_GIT_PATHS
    )
    for candidate in candidates:
        resolved = _resolve_regular_file(candidate)
        if resolved is not None:
            return str(resolved)
    raise TrustedGitError("Git is unavailable at a trusted machine path")


def minimal_git_environment() -> dict[str, str]:
    """Build a fail-closed environment for Git plumbing subprocesses."""

    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
    }
    if os.name == "nt":
        system_root = _windows_system_directory().parent
        environment["SystemRoot"] = str(system_root)
        environment["WINDIR"] = str(system_root)
    return environment


def _trusted_windows_git_candidates() -> tuple[Path, ...]:
    system_drive = Path(_windows_system_directory().anchor)
    candidates: list[Path] = []
    try:
        import winreg

        for registry_view in (
            getattr(winreg, "KEY_WOW64_64KEY", 0),
            getattr(winreg, "KEY_WOW64_32KEY", 0),
        ):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\GitForWindows",
                    0,
                    winreg.KEY_READ | registry_view,
                ) as key:
                    install_path, _value_type = winreg.QueryValueEx(
                        key,
                        "InstallPath",
                    )
            except OSError:
                continue
            if isinstance(install_path, str) and install_path:
                root = Path(install_path)
                candidates.extend((root / "cmd/git.exe", root / "bin/git.exe"))
    except ImportError:  # pragma: no cover - only possible off Windows
        pass
    candidates.extend((
        system_drive / "Program Files/Git/cmd/git.exe",
        system_drive / "Program Files/Git/bin/git.exe",
        system_drive / "Program Files (x86)/Git/cmd/git.exe",
        system_drive / "Program Files (x86)/Git/bin/git.exe",
    ))
    return tuple(dict.fromkeys(candidates))


def _windows_system_directory() -> Path:
    if os.name != "nt":
        raise TrustedGitError(
            "The Windows system directory is unavailable on this platform"
        )
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        get_system_directory = ctypes.windll.kernel32.GetSystemDirectoryW
        length = get_system_directory(buffer, len(buffer))
        if length <= 0 or length >= len(buffer):
            raise OSError("GetSystemDirectoryW failed")
        resolved = Path(buffer.value).resolve(strict=True)
        if not resolved.is_dir():
            raise OSError("Windows system directory is not a directory")
        return resolved
    except (AttributeError, OSError, ValueError) as exc:
        raise TrustedGitError(
            "Cannot resolve the trusted Windows system directory"
        ) from exc


def _resolve_regular_file(candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        return None
    if not resolved.is_file() or metadata.st_nlink != 1:
        return None
    return resolved


__all__ = [
    "TrustedGitError",
    "minimal_git_environment",
    "resolve_trusted_git_executable",
]
