# Mod and modpack management guide

This guide covers the normal solder.py management workflow: create or import a
mod, add compatible versions, create a modpack build, select its contents, and
publish or export it.

## The data model

The three main levels are:

```text
Mod
└── Mod version
    └── Selected in a modpack build
```

A **mod** stores identity and behavior shared by all versions: name, slug,
author, side, type, link, and private notes. A **mod version** stores the Solder
ZIP, hashes, Minecraft version, and modloader compatibility. A **build** selects
one version of each mod and decides whether that selection is optional.

Keeping optional status on the build membership is intentional: the same mod
can be required in one pack and optional in another.

## Add a mod manually

Open **New Mod** and complete these fields:

| Field | Meaning |
| --- | --- |
| Mod Name | Human-readable name shown in management pages and extended API metadata |
| Mod Slug | Repository and API identifier, generated from the name |
| Author | Mod or package author |
| Description | Short public description |
| Mod Website | Public project or documentation link |
| Side | Whether the package belongs on the client, server, or both |
| Type | What kind of Solder package this is |
| Notes (private) | Administrator-only notes; never returned by the read API |

The slug becomes part of every repository filename and URL. Avoid changing it
after versions have been published unless the corresponding repository files
are moved as well.

### Side

| Side | Behavior |
| --- | --- |
| `CLIENT` | Included in client manifests and excluded from server manifests |
| `SERVER` | Included in server manifests and excluded from client manifests |
| `BOTH` | Included for both targets |

The normal Technic build remains available through its compatible default API
view. Side becomes especially important for server manifests and newer export
formats.

### Mod types

| Type | Use |
| --- | --- |
| `MOD` | An ordinary runtime mod. JAR uploads are packaged under `mods/` and can retain a separately hashed raw JAR for newer exporters. |
| `LAUNCHER (MODLOADER)` | A legacy Technic launcher/bootstrap package, commonly the package containing `bin/modpack.jar`. It remains in Technic builds but is excluded from formats whose launcher installs the loader. |
| `RESOURCE PACK` / `RES` | A package primarily containing resource files. |
| `CONFIG` | A package primarily containing configuration or other instance overrides. |
| `MCIL` | A legacy marker for an MCInstance Loader package. Current MRPack and CurseForge exporters obtain the selected downloader directly and exclude this package type. |
| `NONE` | A general Solder package with no more specific type. |

For normal Solder ZIPs, the paths inside the archive still decide where files
are extracted. Selecting `CONFIG`, `RES`, or `NONE` does not rearrange a badly
structured ZIP. Mod type supplies API and export semantics.

When a manual JAR is uploaded, solder.py packages it as `MOD` because it has
been detected as a runtime mod. Existing versions with a verified raw JAR hash
are normalized to `MOD`, except deliberate `MCIL` and `LAUNCHER` packages.

## Add versions manually

Open the mod from **Mod Library** and add a version. A normal JAR upload is the
preferred path: solder.py validates the JAR, gives it a canonical filename,
wraps it in the normal Solder ZIP, and stores MD5 values for both artifacts.
Those raw-JAR details allow MCIL, FileDirector, Modpack Director, and Packwiz to install the JAR
without extracting a Solder ZIP.

Set compatibility deliberately:

- **Minecraft version** limits the version to that Minecraft release. Blank is
  universal.
- **Modloader** limits the version to Forge, NeoForge, Fabric, Quilt,
  LiteLoader, or Vanilla. Blank is loader-agnostic.

A build offers versions whose Minecraft and modloader values match the build,
plus universal values. Required dependencies use the same rules.

For an old Technic Solder version that contains one JAR under `mods/` but has no
raw JAR hash, select **Create MCIL JAR**. solder.py verifies the existing ZIP,
extracts and stores the JAR, and leaves the original version and ZIP in place.

## Link or import mods

### Modrinth

Open **Browse mods**, search, and select **Add mod**. Adding a project stores
its identity and metadata but does not download every release. Compatible
versions appear when the mod is selected in a build. The chosen release is then
downloaded, hash-checked, validated as a JAR, and packaged in the Solder
repository.

If a manual mod already has the same slug, the import links that existing mod
to the Modrinth project. Existing local versions, build selections, metadata,
and repository files are preserved. A mod already managed by Maven or another
Modrinth project is not silently reassigned.

If the slugs differ, open the existing `MOD` entry and use **Link Modrinth**
with the upstream URL, slug, or project ID. The local slug and all versions stay
in place.

### GitHub config repositories

Create or open a `CONFIG` entry, then use **Link GitHub** with a public
repository URL. Manually synchronize a branch, tag, or commit by supplying an
explicit version, or synchronize the newest repository tag. Tagged versions
also appear lazily in the build version selector and participate in **Update
all mods**. GitHub links are rejected for every mod type except `CONFIG`.

Repository metadata is excluded from the generated package, while instance
files and pinned GitHub submodules retain their repository-relative paths. The
result is an ordinary versioned CONFIG ZIP with a Solder MD5, so Technic uses
it like a manually registered config package.

### Maven

Open **Maven**, add a repository, then configure an artifact with its group ID,
artifact ID, optional classifier, and extension. The badge shown on the mod is
the configured Maven repository name.

Minecraft mapping determines which upstream releases are offered:

- **Contained in Maven version** parses the configured part of a version such
  as `1.7.10-9.10.48`.
- **Always one Minecraft version** assigns all releases to one administrator-
  selected Minecraft version.
- **Manual per version** requires an administrator to enable and map each
  release.

See the [integration guide](integrations.md) for repository configuration,
checksums, refresh behavior, and manifest import/export.

### Provider labels

The green **Modrinth** badge, **GitHub** badge, and Maven repository badge identify how future
versions are managed. They are not mod types and do not change the Solder slug
or the already materialized versions. A provider version selected for a build
is still stored as a normal Solder version so Technic Launcher can use it.

## Dependencies

On a mod's version page, search the dependency dropdown and add every mod that
must be present when this mod is used. Dependencies are attached to the mod,
then resolved to the newest compatible version for the target build.

When adding the parent mod, solder.py also adds missing transitive dependencies.
It does not replace a dependency version that is already in the build. Circular
relationships and versions that have no compatible dependency are rejected.

## Create and configure a modpack

Create a modpack from **Modpacks**, then create its first build. Important
build fields are:

| Field | Meaning |
| --- | --- |
| Build number | Version visible to launchers, such as `1.0.0` |
| Minecraft version | Compatibility target for selected mod versions |
| Modloader | Loader family used to filter versions and generate exports |
| Modloader version | Exact loader version; the legacy database column is still named `forge` |
| Clone build | Copies another build's selected versions into the new build |
| Minimum Java Version | Free-form minimum such as `1.8.0_51` |
| Mojang Java Runtime | Launcher runtime override; leave automatic unless a specific Mojang runtime component is required |
| Minimum Memory | Launcher memory requirement in MiB; zero disables it |
| Publish | Makes the build available to API clients |
| Private | Restricts unauthenticated/public access |

After creating builds, choose one **Recommended** and one **Latest** version.
Mark the build currently being maintained so upload pages and dashboard actions
can target it quickly. Pin the modpack if it should remain in the navigation.

Enable optional and server capabilities on the modpack only when clients will
use those shadow manifests.

## Manage a build

Open **Manage** beside a build.

1. Search the mod dropdown and select a mod.
2. Select a compatible local or provider version.
3. Select **List the mod in advanced optional page** if it needs advanced
   delivery choices.
4. Select **Add Mod**. Required dependencies are added automatically.

For a provider-managed mod, merely opening the version selector reads upstream
metadata. The JAR is materialized only after a version is selected. Existing
materialized versions are reused.

**Update all mods** checks provider-managed entries and replaces them only with
available, enabled versions compatible with the build's Minecraft version and
modloader. Review the changed build before publishing it.

Use the build table to change a single selected version, choose which mods are
listed on the advanced-optionals page, or remove a mod. The listing flag is
separate from the Technic/basic optional state configured on that page. A mod
type badge identifies the package role; a separate green
Modrinth or Maven-repository badge identifies the source integration.

### Basic and advanced optionals

Every modpack starts in **Basic** optional mode. Its build memberships use the
normal Technic behavior: required entries are always delivered and optional
entries are delivered only by the optional manifest. In Basic mode, the
**Advanced optionals** page lists those existing legacy optional entries and
ignores the separate advanced work list. Switch the modpack to Advanced when
the pack needs mutually exclusive choices or files that a modern downloader
may offer but Technic must not install.

Advanced mode gives every build membership one Technic/basic state:

| Stored value | State | Technic/basic result |
| --- | --- | --- |
| `0` | Required | Included in the normal build |
| `1` | Optional | Included only when optional packages are requested |
| `2` | Excluded | Never included in a Technic/basic manifest |

Create a named group, choose **Independent checkboxes** or **Choose exactly
one**, and assign build entries to it. Group names become MCIL menu titles and
exact-one FileDirector group labels. An exact-one group always retains one
default choice. An excluded entry must belong to a group so it cannot silently
become unreachable.

FileDirector, Modpack Director, and MCIL consume these advanced groups. Packwiz and the native
Modrinth index retain their basic optional model; an MRPack using FileDirector
or MCIL can still carry grouped fallback packages through that downloader.
Switching a modpack back to Basic is blocked while any membership is Excluded.

For a Forge build used by Technic Launcher, enable **FileDirector files** in
Settings, open **Advanced optionals**, and configure **Technic launcher
delivery**. solder.py downloads and verifies the selected compatible
FileDirector release, creates normal Solder bootstrap/config ZIPs, and adds
those two internal packages to that build's Technic manifest. Mods assigned to
advanced groups are supplied by the versioned FileDirector bundle instead;
unlisted and ungrouped mods continue through normal Solder delivery. If the
setting, advanced mode, public build, or configured groups become unavailable,
solder.py safely falls back to the normal manifest.

## Publish, test, and promote

A practical release flow is:

1. Clone the currently recommended build.
2. Add or update versions in the clone.
3. Keep it private or unpublished while testing.
4. Verify the exact Technic API build and repository downloads.
5. Publish the build.
6. Move **Latest** first if desired, then **Recommended** after validation.
7. Generate and review the changelog between the old and new builds.

For alternative launchers, select **Export** from the build list or the build
editor. Choose Solder-only or hybrid downloads and then
choose whether supported metadata is included in the archive or served from
solder.py. See the [distribution guide](distribution-formats.md) for exact
format behavior.

## Delete with care

Removing a mod from one build only removes that build membership. Deleting a
mod version affects every build that selected it. Deleting the mod removes all
of its versions, dependencies, and related integration configuration from the
database; repository files should be treated as separately backed-up user data.
