# Modrinth integration

These integrations belong to the solder.py management interface. They do not
change the public Technic-compatible read API or expose provider IDs and API
keys to launchers.

Upstream reference: [Modrinth API](https://docs.modrinth.com/api/).

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

## API keys

Modrinth's public read endpoints do not need a key. solder.py does not store a
provider credential for this integration.

## Permissions

- `mods_create` is required to search Modrinth and add a project to the mod
  library.
- `mods_manage` and `modpacks_manage` are required to list or materialize a
  provider version for a build.
- The normal per-modpack permission check still applies.

## Database compatibility

The runtime schema updater adds three nullable fields to existing installations:

- `mods.integration_provider`
- `mods.integration_project_id`
- `modversions.integration_version_id`

Existing Technic Solder and solder.py 1.7.4 rows remain valid because all
provider fields are nullable. The provider metadata stays private to the
management side; normal manifest data continues to use local Solder names,
versions, URLs and hashes.

## Upstream dependencies

Provider version metadata may identify required upstream projects, but solder.py
does not silently create or alter mod-wide dependency relationships during a
lazy import. Configure required dependencies on the local mod page so their
behavior remains explicit and consistent across all local versions.
