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


def _values(value):
    """Return scalar, iterable, or comma-separated compatibility values."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        raw_values = value
    else:
        raw_values = str(value).split(",")
    return [str(item).strip() for item in raw_values if str(item).strip()]


def normalize_minecraft_versions(value):
    """Store one or more Minecraft versions as a canonical CSV string."""
    versions = []
    for item in _values(value):
        if item.upper() == "ANY":
            continue
        if len(item) > 64 or any(ord(character) < 32 for character in item):
            raise ValueError("Minecraft versions contain an invalid value.")
        if item not in versions:
            versions.append(item)
    serialized = ",".join(versions)
    if len(serialized) > 255:
        raise ValueError("Minecraft versions exceed the 255 character limit.")
    return serialized or None


def normalize_modloaders(value):
    """Store one or more loader identifiers as a canonical CSV string."""
    loaders = []
    for item in _values(value):
        normalized = normalize_modloader(item)
        if normalized and normalized not in loaders:
            loaders.append(normalized)
    serialized = ",".join(loaders)
    if len(serialized) > 255:
        raise InvalidModloaderError(
            "Modloader identifiers exceed the 255 character limit."
        )
    return serialized or None


def primary_modloader(value):
    """Return the first loader from a stored compatibility set."""
    normalized = normalize_modloaders(value)
    return normalized.split(",", 1)[0] if normalized else None


def compatibility_values(value, *, modloaders=False):
    """Expose a stored compatibility set without leaking storage details."""
    if modloaders:
        normalized = normalize_modloaders(value)
    else:
        normalized = normalize_minecraft_versions(value)
    return tuple(normalized.split(",")) if normalized else ()


def version_is_compatible(
    version_minecraft,
    version_modloader,
    build_minecraft,
    build_modloader,
):
    """Match null version fields as universal, as existing mcversion does."""
    minecraft_versions = compatibility_values(version_minecraft)
    modloaders = compatibility_values(version_modloader, modloaders=True)
    minecraft_matches = (
        not minecraft_versions or str(build_minecraft) in minecraft_versions
    )
    loader_matches = (
        not modloaders
        or build_modloader is None
        or normalize_modloader(build_modloader) in modloaders
    )
    return minecraft_matches and loader_matches
