"""Shared Minecraft and modloader compatibility helpers."""

import re


_MODLOADER_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")


class InvalidModloaderError(ValueError):
    """Raised when a modloader identifier cannot be stored safely."""


def normalize_modloader(value):
    """Return an uppercase loader identifier, or None for loader-agnostic data."""
    value = str(value or "").strip().upper()
    if value in {"", "ANY"}:
        return None
    if not _MODLOADER_RE.fullmatch(value):
        raise InvalidModloaderError(
            "Modloader must start with a letter and contain only letters, "
            "numbers, hyphens, or underscores."
        )
    return value


def version_is_compatible(
    version_minecraft,
    version_modloader,
    build_minecraft,
    build_modloader,
):
    """Match null version fields as universal, as existing mcversion does."""
    minecraft_matches = (
        version_minecraft is None or str(version_minecraft) == str(build_minecraft)
    )
    loader_matches = (
        version_modloader is None
        or build_modloader is None
        or normalize_modloader(version_modloader)
        == normalize_modloader(build_modloader)
    )
    return minecraft_matches and loader_matches
