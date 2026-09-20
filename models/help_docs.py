"""Bundled management documentation rendered by the Help pages."""

from dataclasses import dataclass
from pathlib import Path
import re

import markdown


@dataclass(frozen=True)
class HelpDocument:
    slug: str
    title: str
    description: str
    filename: str


HELP_DOCUMENTS = (
    HelpDocument(
        "management-guide",
        "Managing mods and modpacks",
        "Create mods, manage versions, build modpacks, dependencies, sides and mod types.",
        "management-guide.md",
    ),
    HelpDocument(
        "integrations",
        "Integrations",
        "Use Modrinth and Maven imports and understand provider-managed versions.",
        "integrations.md",
    ),
    HelpDocument(
        "github-config",
        "GitHub config repositories",
        "Link CONFIG entries, sync tags or refs, package submodules and use .solderpyignore.",
        "github-config.md",
    ),
    HelpDocument(
        "distribution-formats",
        "Distribution formats",
        "Export servers, MCIL, FileDirector, Modpack Director, Packwiz, Modrinth and CurseForge packs.",
        "distribution-formats.md",
    ),
    HelpDocument(
        "technic-solder-users",
        "For Technic Solder users",
        "Compatibility, migration and the features added by solder.py.",
        "technic-solder-users.md",
    ),
    HelpDocument(
        "api",
        "Read API",
        "Technic-compatible and solder.py-specific read endpoints.",
        "api.md",
    ),
    HelpDocument(
        "bootstrap-api",
        "Dedicated bootstrap API",
        "Implement a Solder-aware bootstrap mod with advanced optionals and safe package updates.",
        "bootstrap-api.md",
    ),
    HelpDocument(
        "write-api",
        "Write API",
        "Optional authenticated management API endpoints and payloads.",
        "write-api.md",
    ),
)

_DOCUMENTS_BY_SLUG = {document.slug: document for document in HELP_DOCUMENTS}
_DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"
_INTERNAL_DOCUMENT_LINK = re.compile(
    r"\((?:\.\./)?(?:docs/)?(?P<slug>[a-z0-9-]+)\.md(?P<anchor>#[^)]+)?\)"
)


def get_help_document(slug):
    return _DOCUMENTS_BY_SLUG.get(str(slug or ""))


def render_help_document(document):
    """Render a known bundled Markdown file and keep its doc links in-app."""
    if document not in HELP_DOCUMENTS:
        raise ValueError("Unknown help document.")
    source = (_DOCS_ROOT / document.filename).read_text(encoding="utf-8")

    def internal_link(match):
        slug = match.group("slug")
        if slug not in _DOCUMENTS_BY_SLUG:
            return match.group(0)
        return f"(/help/{slug}{match.group('anchor') or ''})"

    source = _INTERNAL_DOCUMENT_LINK.sub(internal_link, source)
    # These Markdown files are trusted application assets, not user input.
    rendered = markdown.markdown(
        source,
        extensions=("fenced_code", "sane_lists", "tables", "toc"),
        output_format="html5",
    )
    return rendered.replace(
        "<table>", '<table class="table table-striped table-bordered">'
    )
