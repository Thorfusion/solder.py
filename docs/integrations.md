# Modrinth and Maven integrations

These integrations belong to the solder.py management interface. They do not
change the public Technic-compatible read API or expose provider IDs and API
keys to launchers.

Upstream references: [Modrinth API](https://docs.modrinth.com/api/) and the
[Maven repository layout](https://maven.apache.org/repository/layout.html).

## How an import works

1. Open **Browse mods** and search Modrinth for a Minecraft mod.
2. Add the project. solder.py creates a linked mod-library entry but downloads
   no version files.
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

Provider-managed mods cannot receive versions through the manual upload forms.
Their already imported versions can still be rehashed, added to builds, or
deleted like other Solder versions.

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

Maven mods use a server-generated `<repository name>-<mod name>` Solder slug.
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

## Credentials

Modrinth's public read endpoints do not need a key. solder.py does not store a
provider credential for this integration.

The initial Maven implementation supports repositories readable without
credentials. Do not put a username or token in the repository URL; URLs with
embedded credentials are rejected.

## Permissions

- `mods_create` is required to search Modrinth and add a project to the mod
  library.
- `solder_env` is required to configure a Maven repository URL, while
  `mods_create` is required to add an artifact from a configured repository.
- `mods_manage` and `modpacks_manage` are required to list or materialize a
  provider version for a build.
- `mods_manage` is required to edit and refresh Maven version mappings.
- The normal per-modpack permission check still applies.

## Batch integration manifests

**Browse mods** can import a reviewed JSON manifest containing up to 250
Modrinth projects and Maven artifacts. This lets a person or AI resolve a
written mod list to stable Modrinth project IDs or exact Maven coordinates
before an administrator imports it. The importer never guesses from a name.

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
local mod is created; existing mods are never overwritten.

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

Maven configuration is stored in three additive management-side tables:

- `maven_repositories`
- `maven_artifacts`
- `maven_versions`

They do not change the Technic read API schema or response format.

## Upstream dependencies

Provider version metadata may identify required upstream projects, but solder.py
does not silently create or alter mod-wide dependency relationships during a
lazy import. Configure required dependencies on the local mod page so their
behavior remains explicit and consistent across all local versions.
