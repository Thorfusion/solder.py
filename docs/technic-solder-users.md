# solder.py for Technic Solder users

solder.py keeps the normal Technic Solder read API and repository layout while
adding management, automation, server, and alternative-distribution features.
An existing Technic pack does not need to change its launcher URL or rebuild
all of its artifacts merely because its database is moved to solder.py.

This guide describes the differences that matter to an administrator already
familiar with Technic Solder.

## What remains compatible

- The standard `/api/`, `/api/modpack`, `/api/mod`, and build routes retain
  their Technic-compatible defaults.
- Solder package names remain `<slug>-<version>.zip`, with MD5 and file size in
  the database.
- `latest` and `recommended`, published/private state, API keys, clients,
  modpacks, builds, mods, and mod versions keep their familiar purpose.
- Legacy `LAUNCHER` packages remain available to Technic Launcher. This
  includes Forge-era packages containing `bin/modpack.jar`.
- Existing package files and versions are preserved during database migration.

solder.py adds columns, indexes, and separate feature tables. It does not
replace the core Technic records with a new proprietary pack model.

## Main additions

### A more direct management workflow

- Pin frequently used modpacks in the navigation.
- Mark one build as the build currently being worked on.
- Add a newly uploaded version directly to the marked build.
- Clone an existing build when creating the next version.
- Update all eligible provider-managed mods in a build with one action.
- Generate a changelog between two builds.
- Keep private internal notes on a mod without returning them through the API.
- Use the dashboard to find recent changes, stored updates, compatibility
  problems, and distribution preparation work.

### Minecraft and modloader-aware versions

A mod version can target one or more comma-separated Minecraft versions and
modloaders. A blank value is universal. Build selectors, required dependencies, provider imports, and
**Update all mods** use this compatibility metadata instead of offering every
version of a mod to every build.

Builds store both the loader family (`FORGE`, `FABRIC`, `NEOFORGE`, `QUILT`,
`LITELOADER`, or `VANILLA`) and its version. Existing builds with a Technic
`forge` value are treated as Forge builds. Adding or changing a legacy
`LAUNCHER` version also synchronizes its loader metadata to the build.

### Sides, optional packages, and dependencies

Mods can be marked `CLIENT`, `SERVER`, or `BOTH`. A mod in a particular build
can also be optional. A mod may declare other mods as required dependencies;
adding one of its versions to a build resolves compatible direct and transitive
dependencies while preserving versions already selected.

These fields support two opt-in shadow views without changing the default
Technic response:

```text
GET /api/modpack/example-pack/2.0?target=server
GET /api/modpack/example-pack/2.0?optional=true
```

The legacy virtual `2.0-server` and `2.0-optional` suffixes remain available.
Each modpack controls whether the server and optional capabilities are enabled.

Advanced optional groups retain the numeric Technic/basic state: `0` required,
`1` optional, and `2` excluded. By default, this does not change the Technic
contract. A public Forge build can explicitly enable **FileDirector for
Technic** on its advanced-optionals page. solder.py then supplies the selected
FileDirector launchwrapper and remote config as ordinary Solder ZIP packages,
and FileDirector handles only entries assigned to advanced groups. Other
entries continue through Technic's normal Solder flow. The integration falls
back to the normal manifest whenever its compatibility conditions are not met.

### Modrinth, GitHub config, and Maven integrations

Modrinth projects can be linked from **Browse mods** without downloading all
their releases. Maven artifacts can be linked from any repository that exposes
the standard repository layout and `maven-metadata.xml`. In both cases, release
metadata is listed first; a JAR is downloaded, verified, and converted into a
normal Solder package only when that exact version is selected for a build.

Provider-managed versions therefore continue to work with Technic Launcher and
your Solder repository. They are not launcher-side hotlinks to a Maven server.

When a Modrinth project has the same slug as an existing manual mod, solder.py
links the existing mod to the project. Its current metadata, local versions,
build memberships, and repository files remain in place. A slug already owned
by another integration is not overwritten.

If the local and Modrinth slugs differ, the existing mod can instead be linked
manually from its management page. Existing `CONFIG` entries can likewise be
linked to public GitHub repositories. A manual GitHub ref requires an explicit
version, while repository tags are offered lazily in build management and by
**Update all mods**. Generated config archives remain normal Solder packages;
GitHub adds no launcher-side dependency and no provider-specific tables.

Integration manifests can export and import reviewed Modrinth IDs and Maven
coordinates. This is useful for moving a catalog or preparing a large import
outside the management interface.

### More distribution formats

A build can be exported as CSV, MCInstance Loader, FileDirector, Modpack Director, Packwiz,
Modrinth MRPack, CurseForge pack, or a directly importable Prism Launcher
instance. Exports can use only Solder-hosted files or use exact Modrinth files
where mappings exist. Packwiz, FileDirector, and Modpack Director can be served as web-hosted
metadata; supported archives can instead include their configuration.

Global Modrinth-CurseForge sync mappings can map a bootstrap mod's Modrinth project to
its CurseForge project. Enabled mappings are included natively in generated
MRPack and CurseForge archives even when the old build otherwise uses only
Solder-hosted packages. The built-in TX Loader mapping is disabled by default,
and these settings do not change the Technic API or legacy launcher output.

The [distribution usage and format guide](distribution-formats.md) explains
which package types each format can represent and who installs the modloader.

### Server-oriented API extensions

Extended build requests can:

- select client or server packages;
- include optional packages;
- resolve `latest` and `recommended` as build channels;
- return side, mod type, modloader, optional, and dependency metadata;
- return a stable manifest hash and HTTP `ETag`;
- compare an installed build with a target build using `from=<version>`.

Normal Technic Launcher requests do not opt into these extensions and retain
the compact Technic response. See the [read API reference](api.md) for the
complete request and response formats.

### Optional write API and split deployments

The authenticated write API is disabled by default. It can be enabled on a
trusted management deployment, such as one available only through a VPN.
Personal bearer tokens inherit the owner's permissions and modpack access.

`API_ONLY=True` can run the public read API and distribution metadata without
the management interface. `MANAGEMENT_ONLY=True` can keep the management UI off
the public API service. The two processes can share MySQL and repository
storage; only the management process needs database write access unless the
write API is deliberately enabled on the API process.

## Migrating an existing Technic installation

1. Back up the MySQL database and the complete package repository.
2. Stop writers while taking the backup and performing the first migration.
3. Start one solder.py management container with `TECHNIC_MIGRATION=True` and
   a database user allowed to alter the schema.
4. Watch its logs and wait for the migration to finish.
5. Open `/setup`. If the Technic database has no usable administrator, also set
   `NEW_USER=True` temporarily and create one.
6. Turn off `TECHNIC_MIGRATION` and `NEW_USER`, then restart the management
   service.
7. Verify `/api/`, one modpack, one build, and one repository ZIP before moving
   production traffic.
8. Start any separate read-only `API_ONLY` service only after the management
   migration has completed.

The migration recognizes current Technic Solder and solder.py 1.7.4 data. It
adds the current fields and feature tables, preserves old versions, converts
legacy private notes to the current field, restores Forge loader metadata, and
adds query indexes. It does not require a chain of one migration per solder.py
release.

## A safe first tour

After migration, use the existing pack unchanged first. Then:

1. Review mod sides and types in **Mod Library**.
2. Set Minecraft and modloader compatibility on versions that need it.
3. Mark the active build and try dependency resolution on a copied build.
4. Link an existing manual mod to Modrinth by importing its matching slug.
5. Enable optional or server views on one test modpack.
6. Enable only the additional distribution formats you plan to test.

The [management guide](management-guide.md) explains every field and the normal
modpack workflow.
