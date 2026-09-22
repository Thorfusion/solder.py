# GitHub config repository guide

The GitHub integration turns a tagged or explicitly selected repository commit
into an ordinary versioned Solder `CONFIG` package. It is intended for modpack
configuration repositories containing paths such as `config/`, `scripts/`,
`resources/`, or other files that belong at the Minecraft instance root.

It does not create mods, install a modloader, or replace the normal Solder
repository. Every generated version is still a deterministic ZIP with an MD5,
file size, Minecraft version, and optional modloader restriction.

## Link an existing config

1. Create the config in **Mod library > New Mod** and set its type to
   **Config**. An existing manual CONFIG entry can also be used.
2. Open **GitHub** in the navigation bar.
3. Select the CONFIG entry and enter a public GitHub URL or
   `owner/repository`.
4. Select **Link GitHub**.

Only CONFIG entries can be linked. The local name, slug, description, existing
versions, dependencies, and build memberships are retained. solder.py stores
`GITHUB` and GitHub's stable numeric repository ID in the existing integration
columns; it does not add GitHub-specific database tables.

Private repositories are not supported because a generated Solder package is
normally publicly downloadable. Public repositories work without credentials.
An optional `GITHUB_TOKEN` environment variable increases the GitHub API rate
limit but does not enable private-repository distribution.

## Create versions

There are two workflows.

### Tagged versions

Repository tags appear in the version selector when the CONFIG entry is added
to a modpack build. Selecting a tag downloads and packages that exact commit.
**Update all mods** follows the newest tag using natural numeric ordering, so
`v1.10` sorts after `v1.9`.

Tags are the recommended automatic workflow. Existing materialized versions
remain immutable even if a tag is later moved upstream.

### Manual versions

Open the linked CONFIG entry from the mod library or GitHub page. Enter:

- the Minecraft version;
- an optional modloader restriction;
- a branch, tag, or full commit SHA;
- the Solder version to create.

solder.py resolves the reference to an exact commit before packaging it. A
moving branch therefore cannot silently replace an existing Solder version.
Use manual sync for repositories without release tags or when one commit needs
an administrator-assigned version.

## Repository layout

Repository contents are treated as Minecraft instance-root paths:

```text
config/example.cfg
scripts/recipes.zs
resources/example/data.json
servers.dat
```

GitHub's generated archive wrapper is removed. Repository-only content is
excluded automatically, including `.git*`, `.github/`, `docs/`, README,
license, changelog, and root Markdown files. `.solderpyignore` is also excluded.

The remaining files are written to a deterministic ZIP. Symbolic links,
absolute or traversal paths, duplicate destinations, oversized archives,
excessive expanded content, and unsafe GitHub download redirects are rejected.

## `.solderpyignore`

Add a UTF-8 `.solderpyignore` at the repository root for content that belongs
in source control but should not be delivered to players:

```gitignore
# Development and server-only files
*.bak
/servers.json
generated/**
!generated/client-defaults.cfg
```

Supported behavior includes:

- blank lines and lines beginning with `#`;
- `*` and `?` within a path component;
- `**` across directories;
- a leading `/` for repository-root-relative rules;
- directory rules;
- `!` to re-include a later path;
- name-only rules that match at any depth.

Rules are evaluated in order. Root-repository rules also apply to content
provided by pinned submodules. Each submodule may additionally provide its own
`.solderpyignore`. Ignore rules cannot re-enable solder.py's built-in security
and repository-metadata exclusions.

## Submodules

Pinned public GitHub submodules are downloaded at the exact gitlink commit and
placed at their configured repository path. Resolution is recursive within the
documented safety limits.

A `.gitmodules` declaration is not sufficient on its own. The parent commit
must contain a real pinned gitlink at the same path. solder.py deliberately
does not fetch a submodule's moving default branch when that pin is absent,
because the generated config version would not be reproducible.

## Storage and exports

Generated config ZIPs use the same local repository and optional S3/R2 storage
as manual uploads. They can be selected in any normal build and remain
compatible with the Technic read API.

MCInstance Loader, FileDirector, Packwiz, Modrinth, and
CurseForge exports treat the generated version like any other CONFIG package. For downloader-backed
launcher archives, its instance-root contents may be carried by the downloader
configuration rather than copied to the launcher's outer `overrides/`
directory; the resulting installed paths are the same after the downloader
completes.

## Disconnect or recover

Use **Disconnect GitHub** on the config management page to remove the provider
association. Materialized versions, stored ZIPs, dependencies, and build
selections are retained. The CONFIG entry can then be managed manually or
linked again.

If GitHub reports an unavailable repository, verify that it remains public and
that the optional token has not reached its API limit. If a package is empty,
check the built-in exclusions and `.solderpyignore`. If submodule content is
missing, verify that the selected parent commit contains an actual pinned
gitlink rather than only `.gitmodules` text.
