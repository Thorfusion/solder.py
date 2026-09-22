# Modrinth, GitHub config, and Maven integrations

These integrations belong to the solder.py management interface. They do not
change the public Technic-compatible read API or expose provider IDs and API
keys to launchers.

Upstream references: [Modrinth API](https://docs.modrinth.com/api/) and the
[Maven repository layout](https://maven.apache.org/repository/layout.html).

## How an import works

1. Open **Browse mods** and search Modrinth for a Minecraft mod.
2. Add the project. solder.py creates a linked mod-library entry but downloads
   no version files. If a manual mod already has the same slug, that existing
   mod is linked instead; its local versions and build selections are kept.
3. Open a modpack build and select that mod. The version dropdown requests only
   releases matching the build's exact Minecraft version and, when set, its
   modloader.
4. Select a release. solder.py downloads its primary JAR, verifies the provider
   file size and strongest supported hash, checks that it is a valid JAR, then
   packages it as `mods/<slug>-<version>.jar` inside the normal Solder ZIP.
5. The ZIP and raw JAR are written to the configured local repository and, when
   enabled, the configured S3/R2 bucket. The resulting mod version is added to
   the build. A later selection of the same provider version reuses it without
   another download.

When the selected Modrinth version declares required dependencies, solder.py
also imports or links those projects, materializes their compatible versions,
and records them in the normal Solder dependency list. A dependency with an
explicit Modrinth version ID uses that exact compatible release; a project-only
dependency uses its newest release compatible with the build. Nested required
dependencies are handled recursively. Optional, incompatible, and embedded
Modrinth dependency types are not promoted to required Solder dependencies.

Provider-managed mods cannot receive versions through the manual upload forms.
Their already imported versions can still be rehashed, added to builds, or
deleted like other Solder versions.

A matching manual slug is converted only when it has no existing integration.
solder.py changes the integration provider and project ID on the existing mod
row; it does not replace the mod, its reviewed metadata, versions, repository
files, dependencies, or build memberships. A slug already linked to Maven or a
different Modrinth project produces an error instead of being reassigned.

When the local slug differs, open the existing `MOD` entry and use **Link
Modrinth**. Enter a Modrinth project URL, slug, or project ID. The local slug,
metadata, versions, dependencies, repository files, and build memberships stay
unchanged; only the existing provider fields are attached. **Disconnect
Modrinth** removes that association without deleting materialized versions.

## GitHub config repositories

The dedicated [GitHub config repository guide](github-config.md) documents the
complete management workflow, archive rules, `.solderpyignore`, submodules,
storage, and troubleshooting.

An existing mod whose type is `CONFIG` can be linked to a public GitHub
repository from its management page. GitHub cannot be attached to `MOD`,
`LAUNCHER`, or other package types. The stable numeric GitHub repository ID is
stored in the existing `mods.integration_project_id` field, with `GITHUB` in
`mods.integration_provider`; no GitHub-specific database table is used.

There are two versioned workflows:

- **Manual ref:** enter a branch, tag, or full commit SHA together with an
  explicit config version, Minecraft version, and optional modloader. A branch
  is therefore never allowed to overwrite an existing Solder version silently.
- **Repository tags:** select **Sync latest tag**, or choose a compatible tag
  from the config entry's version dropdown in a build. Tags are sorted by their
  numeric components, materialized lazily, and recorded as immutable normal
  Solder versions. **Update all mods** follows the newest available tag.

solder.py downloads the exact resolved commit, strips GitHub's generated
top-level archive directory, removes repository-only files such as `.github`,
`.git*`, README, license, changelog, and documentation files, and writes the
remaining instance-root files to a deterministic Solder ZIP. Pinned GitHub
submodules are downloaded at their recorded commits and placed at their
configured paths. Symbolic links, unsafe paths, oversized archives, duplicate
paths, and excessive expanded content are rejected.

Add a UTF-8 `.solderpyignore` file to the repository root when files that
belong in source control should not be shipped to clients. Its rules are
evaluated in order and use familiar gitignore-style syntax:

```gitignore
# Development and server-only files
*.bak
/servers.json
generated/**
!generated/client-defaults.cfg
```

`*`, `?`, `**`, root-relative `/patterns`, directory patterns, comments, and
`!` re-inclusion are supported. A name without `/` matches at any depth. The
file is not included in the generated ZIP. The root repository's rules also
apply to files supplied by pinned submodules; a submodule may additionally
provide its own `.solderpyignore`. Ignore rules cannot restore solder.py's
built-in security and repository-metadata exclusions.

A `.gitmodules` entry alone is not versioned content. The parent repository must
also contain a pinned Git submodule entry at that path. Stale declarations with
no pinned commit are not fetched from a moving default branch.

The generated package is stored in the same local repository and optional
S3/R2 bucket used by solder.py's upload extension. Its ZIP MD5 and file size are
recorded in the ordinary `modversions` table. Technic-compatible API responses
therefore continue to expose an ordinary Solder config package; this does not
change Technic Solder's metadata-only/manual-MD5 model.

Packwiz, FileDirector, and Modpack Director remain dynamic public solder.py
routes. In the Docker setup, clients connect to Caddy and Caddy reverse-proxies
`/packwiz/*`, `/filedirector/*`, and `/modpackdirector/*` to the API container;
Caddy does not look for those generated responses in the static `/mods`
directory.

## Maven repositories

Maven support uses the standard repository layout rather than a vendor API.
It works with ordinary static Maven hosting as well as repository managers such
as Artifactory or Nexus when their standard Maven URL is configured.

1. Open **Maven** and add a repository name and base URL. Use the actual Maven
   repository root, such as `https://maven.example/repository/releases/`, not a
   web-interface URL.
2. Add a Maven mod with its `groupId`, `artifactId`, optional classifier, and
   `jar` extension. A classifier is part of the artifact selection: `ALL`,
   `Core`, and `Generators`, for example, should be separate Solder mods when
   they are independently installable.
3. Select how the upstream version identifies Minecraft:
   - **Contained in Maven version**: enter a format containing exactly one
     `{minecraft}` and `{version}` placeholder. Examples include
     `{minecraft}-{version}`, `{version}-{minecraft}`, and
     `{version}+mc{minecraft}`.
   - **Always one Minecraft version**: enter the Minecraft version applied to
     every release of that artifact.
   - **Manual per version**: assign values after loading metadata.
4. Confirm that the artifact may be downloaded and redistributed through the
   Solder repository. Maven publication by itself is not a redistribution
   license.
5. Manage the artifact to refresh `maven-metadata.xml`, inspect every upstream
   release, make manual corrections, or disable releases.

The exact upstream Maven version is stored separately from the local Solder
version. That keeps download coordinates exact while preserving Solder's
`<minecraft>-<mod version>` naming. Manual mappings are not overwritten by
later metadata refreshes. Releases that do not match the configured rule remain
disabled, and releases no longer present upstream are retained for history but
marked unavailable.

The modloader configured on an artifact is copied into rule-generated version
mappings. Individual version rows may override it. A blank modloader is
loader-agnostic.

Maven mods use a server-generated `<mod name>-<repository name>` Solder slug.
The form initially copies the artifact ID into the mod name, then uses the same
slug conversion as the normal new-mod form. You can edit the mod name before
submitting; the server regenerates and validates the final slug. Repository
badges identify the exact configured Maven source rather than only "Maven".

Standard `.sha512`, `.sha256`, `.sha1`, and `.md5` sidecars are checked in that
order. When the repository publishes no sidecar, solder.py still streams the
download through its size limit, calculates its local hashes, and validates the
JAR structure. Metadata and download redirects are confined to the configured
repository origin. Standard timestamped `-SNAPSHOT` filenames are resolved from
the version-level `maven-metadata.xml`.

Enable **Use the Maven JAR URL directly** on an artifact when its repository is
publicly reachable over HTTPS. Bootstrap manifests then give SolderPy Loader
the exact Maven artifact URL followed by the Solder-hosted JAR fallback.
Timestamped snapshots are resolved from version metadata when the manifest is
generated. Both sources use the raw-JAR MD5 and size recorded during import.

Every stored raw-JAR version also has an optional management-side HTTPS URL
override. Open **Manage** beside that version to inspect its provider IDs,
compatibility, hashes, timestamps, and build assignments or change the
override. Its source priority is override, native Modrinth or enabled Maven,
then Solder. The override filename may differ because the checksum covers file
contents, not its name. Saving an override downloads it once on the server and
requires its bytes to match the stored raw-JAR MD5; a mismatch leaves the
previous override unchanged. Successful verification also records the JAR file
size.

## Credentials

Modrinth's public read endpoints do not need a key. solder.py does not store a
provider credential for this integration. Public GitHub repositories also work
without credentials. Set the optional `GITHUB_TOKEN` environment variable to
raise GitHub's API rate limit; private repositories are intentionally rejected
because the generated Solder package is publicly distributable.

The initial Maven implementation supports repositories readable without
credentials. Do not put a username or token in the repository URL; URLs with
embedded credentials are rejected.

## CurseForge API terms and data handling

CurseForge API access is governed by the
[CurseForge third-party API terms](https://support.curseforge.com/support/solutions/articles/9000207405-curse-forge-3rd-party-api-terms-and-conditions).
The key is issued to a specific developer and external application. Never
commit it, put it in a generated archive, expose it to a browser or launcher,
or share one installation's key with another operator. A self-hosted operator
needs an independently approved key unless CurseForge gives written approval
for another deployment model.

Treat every value returned by a CurseForge API as request-scoped data. In
particular, never persist or cache returned project or file metadata, file IDs,
names, hashes, sizes, download URLs, timestamps, pagination data, or complete
responses in MySQL, files, queues, logs, browser storage, or an application
cache. This rule also applies to identifiers returned after an author upload.
Publication history may retain solder.py's local build ID, archive digest,
attempt status, and patch number, but not a remote identifier learned from a
CurseForge response.

The following data is not API-derived and may be stored:

- a project ID manually entered by an administrator;
- a CurseForge file ID manually copied by an administrator for an exact local
  Modrinth-backed mod version;
- the installation's server-side API key;
- a user's own author-upload token and manually entered publishing project ID;
  and
- local Solder build, package, audit, and archive-digest data.

When no manual file ID exists, CurseForge manifest generation queries compatible
files at export time, matches entirely in memory, places the chosen project and
file ID only in the returned archive, closes the response, and discards the
metadata. API-derived file IDs are never copied into the manual-ID table. Do not
retain a server-side copy of the generated manifest. Failed or ambiguous
matching must stop the export rather than save candidates for later selection.
solder.py must not download, mirror, proxy, or redistribute a CurseForge-hosted
mod file; the native manifest leaves delivery to the CurseForge-compatible
client.

The terms also prohibit concealing API access through a proxy or VPN. Hosting
the management interface behind a VPN is separate, but outbound CurseForge API
traffic must not use a VPN or proxy to disguise the server's identity or
location. Keep access administrator-triggered, honor API errors and quotas,
and do not add background polling. CurseForge reviews applications for effects
on author earnings, service load, and author distribution consent; describe
the exact export-time behavior when
[applying for a key](https://support.curseforge.com/support/solutions/articles/9000208346-about-the-curseforge-api-and-how-to-apply-for-a-key).

## Permissions

- `mods_create` is required to search Modrinth and add a project to the mod
  library.
- `solder_env` is required to configure a Maven repository URL, while
  `mods_create` is required to add an artifact from a configured repository.
- `mods_manage` and `modpacks_manage` are required to list or materialize a
  provider version for a build.
- `mods_manage` is required to link an existing mod to Modrinth or GitHub and
  to manually synchronize a GitHub config ref or tag.
- A managed mod's version page lists provider versions that have not yet been
  stored locally. Importing one there uses its selected Minecraft version and
  modloader, verifies and packages it through the same path used by the build
  editor, and leaves already imported provider IDs out of the upstream table.
- `mods_manage` is required to edit and refresh Maven version mappings.
- The normal per-modpack permission check still applies.

## Batch integration manifests

**Browse mods** can import a reviewed JSON manifest containing up to 250
Modrinth projects and Maven artifacts. This lets a person or AI resolve a
written mod list to stable Modrinth project IDs or exact Maven coordinates
before an administrator imports it. The importer never guesses from a name.

Use **Export configured integrations** on the same page to download the
currently configured Modrinth and Maven mods in this format. The export uses
the reviewed local name, description, author, project link, and side together
with each portable provider identifier. Maven entries also include their
repository, exact coordinates, modloader, and Minecraft mapping rule. It does
not contact an upstream provider and does not include local mod versions,
downloaded files, internal notes, users, tokens, or credentials. The generated
file can be reviewed, edited, and imported into another solder.py installation.

Start from the
[example integration manifest](../static/examples/integration-manifest.json).
Its root contract is:

```json
{
  "format": "solder.py-integration-manifest",
  "version": 1,
  "mods": []
}
```

A Modrinth item requires `provider: "modrinth"` and `project_id`. A Maven item
requires `provider: "maven"`, a `repository` object containing `name` and
`url`, plus `group_id` and `artifact_id`. Maven items can also set `classifier`,
`modloader`, and a Minecraft mapping:

```json
"minecraft": {
  "mode": "EMBEDDED",
  "pattern": "{minecraft}-{version}"
}
```

Use `FIXED` with `version`, or `MANUAL` when releases will be mapped later.
Both provider types accept reviewed `name`, `description`, `author`, `link`,
and `side` (`BOTH`, `CLIENT`, or `SERVER`). Metadata is applied only when a new
local mod is created. A matching manual Modrinth slug is linked without
overwriting its existing metadata.

The complete file is validated before provider calls begin. Files are limited
to 512 KiB. Maven entries require `mods_create`, `solder_env`, and the form's
redistribution confirmation because they may add a trusted repository URL.
Modrinth-only manifests require `mods_create`. Individual provider failures are
reported while other valid items continue importing.

## Database compatibility

The runtime schema updater adds three nullable fields to existing installations:

- `mods.integration_provider`
- `mods.integration_project_id`
- `modversions.integration_version_id`

Existing Technic Solder and solder.py 1.7.4 rows remain valid because all
provider fields are nullable. The provider metadata stays private to the
management side; normal manifest data continues to use local Solder names,
versions, URLs and hashes.

GitHub config sources reuse these nullable provider fields and add no tables.

Maven configuration is stored in three additive management-side tables:

- `maven_repositories`
- `maven_artifacts`
- `maven_versions`

They do not change the Technic read API schema or response format.

## Upstream dependencies

Materializing a Modrinth version imports its required upstream projects and
stores the relationships in the existing `mod_dependencies` table. They are
therefore visible and editable in the mod's **Required dependencies** list and
are added transitively when the parent is added to a build. Existing projects,
versions, and relationships are reused.

Solder dependencies are mod-wide, while Modrinth reports them per version.
Consequently, an automatically discovered requirement remains attached to the
local mod for its other versions as well. Imports add or reuse requirements but
do not silently delete an existing dependency merely because a later upstream
version omits it. Review or remove such a relationship on the local mod page if
the upstream project's requirements genuinely changed.
