# Distribution usage and format guide

solder.py keeps the Technic Solder API as its primary distribution interface,
but the same build can also be exported for MCInstance Loader, FileDirector,
Modpack Director, Packwiz, Modrinth, CurseForge, or Prism Launcher. This guide covers both the
management workflow and the files each format receives.

## Choose an output

| Output | Use it for | Delivery |
| --- | --- | --- |
| CSV | Auditing a build or moving a simple mod list into another tool | Downloaded CSV |
| MCInstance Loader (MCIL) | A self-contained MCIL pack with downloadable mods and bundled overrides | Downloaded `.mcinstance` archive |
| FileDirector | Installing individual files or Solder ZIPs with optional and side metadata | Downloaded config ZIP or hosted config |
| Modpack Director | Runtime installation using a FileDirector-compatible bundle plus pack/update metadata | Downloaded config ZIP or hosted config |
| Packwiz | A standard Packwiz pack made from raw JAR-ready mods | Downloaded metadata ZIP or hosted metadata |
| Modrinth | A Modrinth `.mrpack`, with exact Modrinth files kept native | Downloaded `.mrpack` archive |
| CurseForge | A CurseForge pack that bootstraps MCIL, FileDirector, or Modpack Director | Downloaded CurseForge ZIP |
| Prism Launcher | A directly importable bootstrap or self-contained instance | Downloaded Prism instance ZIP |

The Technic API remains the right choice for Technic Launcher. Its builds still
include legacy `LAUNCHER` packages such as a Forge `bin/modpack.jar`; the newer
archive formats let their target launcher install the declared modloader
instead.

## Prepare solder.py

### Configure repository URLs

The exporters use these application settings:

| Setting | Purpose |
| --- | --- |
| `PUBLIC_REPO_LOCATION` | Public HTTP or HTTPS base URL from which players can download Solder ZIPs and raw JARs |
| `MD5_REPO_LOCATION` | Server-side repository path or URL used to inspect and hash existing packages; a local path is allowed |
| `APP_URL` | Public HTTP or HTTPS URL of solder.py, used in hosted FileDirector and Modpack Director pointers |
| `CURSEFORGE_API_KEY` | CurseForge third-party API key; required only for CurseForge archive exports |

`PUBLIC_REPO_LOCATION` and `MD5_REPO_LOCATION` deliberately serve different
purposes. A local filesystem path is useful for fast server-side hashing, but it
cannot be written into a client download manifest. In the recommended proxy
layout Caddy serves the public mod repository directly and reverse-proxies the
solder.py application, including `/packwiz/*`, `/filedirector/*`, and
`/modpackdirector/*`.

### Enable the formats

All distribution formats are disabled by default. Open **Settings > Env
Settings**, enable each required format, and select **Save distribution
settings**. MCInstance Loader, Packwiz, FileDirector, Modpack Director,
Modrinth MRPack, CurseForge, and Prism Launcher have independent switches.

These switches are stored in MySQL, not only in the management process. A
separate process started with `API_ONLY=True` therefore uses the same settings
and can serve Packwiz, FileDirector, and Modpack Director files without
exposing the management interface. Disabling a format does not remove raw-JAR hashes, integration
mappings, or other build data.

### Modrinth-CurseForge sync

Some bootstrap mods must be installed by the target launcher instead of being
downloaded after Minecraft starts. Open **Settings > Modrinth-CurseForge sync** to map
a Modrinth project to its equivalent numeric CurseForge project ID. Each
mapping also records whether the mod is client-only, server-only, or required
on both sides.

An enabled mapping is native in hybrid Modrinth and CurseForge archives:

- the newest release compatible with the build's exact Minecraft version and
  modloader is resolved when the archive is exported;
- a Modrinth export adds the verified project file to `modrinth.index.json`;
- a CurseForge export adds the matching project and file IDs to
  `manifest.json`;
- **Override Solder-only** may be enabled for a mapping that must remain native
  even when **Solder only** is selected; otherwise that source mode does not
  apply the mapping; and
- an existing Modrinth-integrated build package with the same project ID is
  removed from the fallback downloader list so it is not installed twice.

TX Loader is included as a built-in, client-only mapping between Modrinth
project `eh8us8FY` and CurseForge project `706505`. It is disabled by default.
Enable it only for packs that use TX Loader's early resource and asset loading
behavior. More mappings can be added without changing solder.py. A CurseForge
API key is needed to resolve CurseForge files, while Modrinth resolution does
not use that key.

These mappings affect only generated Modrinth and CurseForge archives. They do
not add packages to a build and do not change the Technic-compatible API, so
they can also be used with builds migrated from Technic Solder.

Use **Export list** to download a versioned JSON copy of every mapping and its
enabled flags. **Import list** validates the complete file before changing the
database, updates matching Modrinth or CurseForge project IDs, adds new
mappings, and preserves built-in deletion protection. This file contains no
API key or other credential.

### Advanced optional delivery

In Basic mode, the **Optional** checkbox in the build editor directly controls
the legacy Technic optional state. In Advanced mode, select **List the mod in
advanced optional page** while adding a mod, or use the same checkbox in the
build table. This work-list flag is separate from the normal build optional
state. The **Advanced optionals** page stores named groups
while retaining one Technic/basic state per configured entry:
`0` is required, `1` is optional, and `2` is excluded. State `2` is never
returned to Technic, Packwiz, or the native MRPack file list. It remains
available to FileDirector, Modpack Director, or MCIL only when assigned to an active group.

FileDirector and Modpack Director render independent choices as checkboxes and
exact-one named groups as radio choices. MCIL renders every named group as a separate menu,
using the saved group name, order, default selections, and min/max choice
rules. Basic mode ignores the saved advanced groups, and returning to Basic is
blocked until all excluded entries have been changed to required or optional.
While in Basic mode, the page lists only the build's existing legacy optional
entries (`optional = 1`), regardless of the saved advanced work list.

### FileDirector choices in Technic Launcher

For public, published Forge builds, the advanced page can enable FileDirector
inside Technic Launcher. Select a compatible FileDirector version from
Modrinth. solder.py verifies the JAR and writes two internal Solder ZIPs: one
extracts the launchwrapper JAR to `mods/`, and the other extracts a versioned
remote pointer to `config/mod-director/`. These are virtual API entries, not
mods in the management library.

Only packages assigned to an advanced group are removed from Technic's normal
package list and exposed at:

```text
https://solder.example.com/filedirector/example-pack/1.0/technic.bundle.json
```

Ungrouped packages keep normal Solder behavior. Disabling FileDirector files,
switching the modpack to Basic mode, removing all grouped choices, or making
the build private deactivates delegation automatically. The bootstrap uses
fixed extracted filenames for older launchers; current Technic Launcher orphan
cleanup also removes superseded extracted files during pack updates.

### Prepare the build

Before exporting:

1. Set the build's Minecraft version and modloader.
2. Set the modloader version when the selected format needs it. For old
   Technic builds, solder.py identifies a build with a Forge version as
   `FORGE` automatically.
3. Check each mod's side and optional status. These values are carried into
   formats that support them.
4. Publish the build and make it non-private if clients will use hosted
   Packwiz, FileDirector, or Modpack Director files. Downloaded management-side exports may still
   be generated according to the user's normal modpack permissions.

Raw JARs give the best result. New JAR uploads store both the Solder ZIP MD5 and
the raw JAR MD5. For a legacy or imported Technic version, use **Create MCIL
JAR** on the mod version page. solder.py verifies the existing ZIP, extracts
the single JAR under `mods/`, stores it under its canonical
`<slug>-<version>.jar` name, and records its MD5. It reads from the local
repository first and then falls back to `MD5_REPO_LOCATION`; configured S3 or
R2 storage is updated as well.

## Export a build

From a modpack's build list, select **Export** for the required build. From the
build editor, select **Export** beside **Update all mods**. Both open the same
export window. The external downloader version lists are loaded only when this
window is requested, so normal build management does not wait for Modrinth or
CurseForge.

The window contains a shared group of settings:

- **Download source — Solder only** uses `PUBLIC_REPO_LOCATION` for all build
  packages.
- **Download source — Hybrid** uses the exact Modrinth CDN file for a
  Modrinth-mapped version and Solder for manual, Maven, legacy, configuration,
  and resource packages.
- **Forge version** temporarily overrides the exported modloader version. It
  does not change the saved build.
- **Build export delivery — Include configuration and metadata in the archive**
  puts generated files in the downloaded ZIP.
- **Build export delivery — Use web-hosted configuration and metadata**
  produces a hosted Packwiz entry point or Director remote pointer.
- **Hosted build** can pin the configuration to this build or follow the
  modpack's `latest` or `recommended` channel.

Formats use only the controls they support. CSV ignores the shared controls,
and MCInstance Loader always includes its configuration in the archive. Prism
uses source and delivery when MCIL or FileDirector is selected, and still uses
a temporary modloader-version override when one is entered. Modrinth and
CurseForge also show a compatible fallback downloader selector.

Hybrid generation resolves the stored exact Modrinth project and version
mapping when the export is made. The upstream filename, URL, SHA-1, SHA-512,
and file size are validated at that time and are not duplicated in
`modversions`.

## CSV

**Export CSV** downloads a Technic-style build list with these columns:

```text
mod_name,mod_slug,version,md5,filesize
```

This export is always available to an authenticated user with access to the
modpack and does not require a distribution-format switch.

## MCInstance Loader

Enable **MCInstance Loader exports**, open a build's export window, and select
**Export MCIL**. solder.py produces the
[MCInstanceLoader 2.7 archive format](https://github.com/HRudyPlayZ/MCInstanceLoader/tree/1.7.10)
with a `.mcinstance` filename. `PUBLIC_REPO_LOCATION` is required because raw
JAR entries contain client-facing download URLs.

### Archive layout

```text
metadata.packconfig
resources.packconfig
optionals.packconfig
overrides/
client-overrides/
server-overrides/
```

`metadata.packconfig` declares the pack, Minecraft version, and modloader for a
standalone MCIL export. `resources.packconfig` contains the downloadable JARs,
including destination, side, optional status, URL, and verified MD5.
`optionals.packconfig` describes the client-visible optional choices. Advanced
named groups become separate MCIL menus; exact-one groups use a minimum and
maximum of one selection.

Solder packages map into the archive as follows:

| Solder package | MCIL result |
| --- | --- |
| `MOD` with a raw JAR MD5 | Downloadable resource in `mods/` |
| Required `MOD` without a raw JAR | Solder ZIP is verified and unpacked into an override directory |
| `CONFIG`, `RES`, or `NONE` | Solder ZIP is verified and unpacked into an override directory |
| `MCIL` | Excluded; it represents the downloader rather than pack content |
| `LAUNCHER` | Excluded; its `bin/` content is specific to Technic Launcher |

`BOTH`, `CLIENT`, and `SERVER` packages are placed under `overrides/`,
`client-overrides/`, and `server-overrides/` respectively when their ZIP is
bundled. Archive paths are checked before extraction and duplicate destination
paths fail the export instead of silently overwriting a file.

An optional package must have a verified raw JAR because MCIL cannot toggle
the individual contents of a bundled Solder ZIP. MCIL 2.7 exposes optional
choices on the client, so an optional server-only package is also rejected.

The browser calculates upload hashes for immediate feedback, but the server
independently verifies the received Solder ZIP and extracted JAR before saving
the version. During export, legacy package ZIPs are checked against their
stored MD5 before they are unpacked.

In hybrid mode, an exact Modrinth-mapped JAR uses its validated Modrinth CDN
URL; every other resource uses Solder. When MCIL is embedded as the fallback in
a Modrinth or CurseForge archive, its `metadata.packconfig` omits the modloader
section because the outer launcher owns modloader installation. MCIL has no
FileDirector-style remote configuration, so its config is always included in
the exported archive.

Do not add the MCInstance Loader JAR itself as an ordinary Solder package for
these exports. Modrinth and CurseForge exports select a current compatible MCIL
release from the target platform API and bootstrap it as needed.

## FileDirector

Enable **FileDirector files**. A bundled export downloads a ZIP containing:

```text
config/mod-director/solder.bundle.json
```

A hosted export instead opens a small `.remote.json` configuration that points
at the generated bundle. Choose this build, `latest`, or `recommended` in the
export window. FileDirector 1.6 and newer support the hosted arrangement.

The main public bundle is:

```text
https://solder.example.com/filedirector/example-pack/latest/mods.bundle.json
```

Other available views are:

```text
https://solder.example.com/filedirector/example-pack/latest/required.bundle.json
https://solder.example.com/filedirector/example-pack/latest/optional.bundle.json
https://solder.example.com/filedirector/example-pack/latest/technic.bundle.json
https://solder.example.com/filedirector/example-pack/latest/mods.remote.json
https://solder.example.com/filedirector/example-pack/latest/version.txt
```

Use `mods.bundle.json` for the complete build. The `required` and `optional`
bundles exist for clients that intentionally load those subsets; do not load
them alongside `mods.bundle.json`. `modrinth-fallback.bundle.json` is reserved
for generated MRPack archives and omits packages already installed natively by
Modrinth.

Raw-JAR-ready mods are downloaded as JARs and checked with their stored JAR
MD5. Other ordinary Solder packages are downloaded as ZIPs, checked with their
ZIP MD5, extracted at the instance root, and deleted after extraction. Client
and server side metadata and optional selection are preserved; basic optional
files default to not selected. Advanced independent choices use FileDirector's
standalone checkbox key, while exact-one choices share their group name and
become radio buttons. `MCIL` and `LAUNCHER` packages are excluded.

For hybrid public files, add `?source=hybrid`, for example:

```text
https://solder.example.com/filedirector/example-pack/latest/mods.remote.json?source=hybrid
```

See FileDirector's upstream documentation for
[bundle configs](https://github.com/TerraFirmaCraft-The-Final-Frontier/FileDirector/wiki/Config-Type:-Bundle),
[remote configs](https://github.com/TerraFirmaCraft-The-Final-Frontier/FileDirector/wiki/Config-Type:-Remote),
and [remote modpack versions](https://github.com/TerraFirmaCraft-The-Final-Frontier/FileDirector/wiki/Modpack).

## Modpack Director

Enable **Modpack Director exports** and select **Export Modpack Director**.
Modpack Director is a FileDirector fork and accepts the same `.bundle.json`
and `.remote.json` files under `config/mod-director/`. solder.py therefore uses
the same package filtering, MD5 checks, side metadata, and advanced optional
groups for both implementations.

A bundled export contains:

```text
config/mod-director/modpack.json
config/mod-director/solder.bundle.json
```

A hosted export replaces the bundle with `solder.remote.json`. Its public
metadata is available under:

```text
https://solder.example.com/modpackdirector/example-pack/latest/mods.bundle.json
https://solder.example.com/modpackdirector/example-pack/latest/mods.remote.json
https://solder.example.com/modpackdirector/example-pack/latest/version.txt
```

`modpack.json` identifies the pack and its local version. When `APP_URL` is
configured, it also points to the selected exact, `latest`, or `recommended`
version endpoint so Modpack Director can report an outdated pack. solder.py
does not refuse launch automatically.

Install the appropriate Modpack Director JAR separately when using the config
ZIP directly. For a CurseForge pack export, solder.py can instead add the
selected official CurseForge project (`969109`) and file to `manifest.json`.
The official project currently has no Modrinth release, so it is intentionally
not shown in MRPack or Prism's Modrinth-backed downloader selector. See the
[upstream project](https://github.com/juanmuscaria/ModpackDirector) for its
LaunchWrapper, ModLauncher, universal, and standalone variants.

## Packwiz

Enable **Packwiz files**. A bundled export contains:

```text
pack.toml
index.toml
mods/{solder-mod-slug}.pw.toml
```

A hosted export opens the `pack.toml` entry point. Exact build versions,
`latest`, and `recommended` are supported:

```text
https://solder.example.com/packwiz/example-pack/1.0/pack.toml
https://solder.example.com/packwiz/example-pack/latest/pack.toml
https://solder.example.com/packwiz/example-pack/recommended/pack.toml
```

Channel entry points redirect to the resolved exact build so `pack.toml`,
`index.toml`, and every `.pw.toml` stay on one version while Packwiz verifies
their SHA-256 metadata hashes. Hybrid mode has `hybrid` in the path:

```text
https://solder.example.com/packwiz/example-pack/latest/hybrid/pack.toml
```

The downloaded JAR itself uses the MD5 already verified by solder.py. Side and
optional metadata are retained, with optional mods disabled by default. The
build must have a supported modloader and its version must be set.

Packwiz cannot extract a Solder package ZIP through its standard metadata
format. The generated index therefore includes only `MOD` packages with a
stored raw JAR MD5. `CONFIG`, `RES`, `NONE`, legacy mods without a raw JAR,
`MCIL`, and `LAUNCHER` remain in the Technic build but are omitted from the
Packwiz view. Public responses report the omitted count in
`X-Solder-Excluded-Packages`. Convert an eligible legacy mod with **Create MCIL
JAR**; maintain configuration overrides in the Packwiz pack separately.

See the upstream [Packwiz pack format reference](https://packwiz.infra.link/reference/pack-format/pack-toml/)
and [packwiz-installer guide](https://packwiz.infra.link/tutorials/installing/packwiz-installer/)
for client setup.

## Modrinth MRPack

Enable **Modrinth MRPack exports** and select **Export Modrinth**. The result is
a `.mrpack` containing `modrinth.index.json` and, when required, fallback
downloader configuration under `overrides/`.

In hybrid mode, exact Modrinth versions become native MRPack entries using the
original CDN URL, filename, size, SHA-1, and SHA-512. Manual, Maven, legacy,
configuration, and resource packages are delivered through the selected MCIL
or FileDirector fallback. Maven repository URLs are never placed directly in
the pack. A no-downloader export is allowed only when every actual package is
an exact compatible Modrinth version.

In Solder-only mode, the MRPack bootstraps the selected downloader and all
actual packages use the Solder repository.

The downloader selector queries Modrinth for MCInstance Loader and FileDirector
releases compatible with the build's Minecraft version and Forge. The newest
result is marked recommended, and the selected ID is resolved again during
export. The downloader is not created as a Solder mod and its JAR is not stored
in the Solder repository. Its corresponding distribution switch must be
enabled before it appears as a choice.

FileDirector may use a bundled config or a hosted pointer. Hosted delivery
requires a published, non-private build and can follow this build, `latest`, or
`recommended`. MCIL configuration is always bundled.

## CurseForge pack

Enable **CurseForge exports**, configure `CURSEFORGE_API_KEY`, and select
**Export CurseForge** from a compatible Forge build. The ZIP contains
`manifest.json` and `overrides/`. Its native `files` array contains the selected
MCInstance Loader, FileDirector, or Modpack Director release plus any enabled Modrinth-CurseForge sync
overrides.

The selector loads compatible files from the official CurseForge API. During
export, solder.py verifies that the selected file still belongs to the expected
project and supports the build's Minecraft version. The API key is sent only
to `https://api.curseforge.com/v1` and is never written into the archive.

In Solder-only mode, the downloader installs every actual build package from
Solder. In hybrid mode, exact Modrinth-mapped versions use their validated
Modrinth CDN URLs inside the selected downloader configuration, while manual,
Maven, legacy, configuration, and resource packages continue to use Solder.
Ordinary Modrinth project mappings are not guessed or translated into
CurseForge IDs. Only explicit mappings under **Settings > Modrinth-CurseForge sync**
are installed natively on both platforms.

FileDirector and Modpack Director configuration can be included in the archive
or hosted by solder.py and pinned to this build, `latest`, or `recommended`.
MCIL configuration is always included. Modpack Director is resolved from its
official CurseForge project and supports compatible Forge and NeoForge files.

## Prism Launcher instance

Enable **Prism Launcher instance exports** and select **Export Prism**. Choose
a compatible MCInstance Loader or FileDirector release to create a lightweight
bootstrap, or choose **Self-contained archive** when neither downloader can be
used. Prism Launcher and compatible MultiMC importers can open either ZIP
directly. Every export contains:

```text
instance.cfg
mmc-pack.json
.minecraft/
```

`mmc-pack.json` uses format version 1 and declares Minecraft plus the saved
Forge, NeoForge, Fabric, Quilt, or LiteLoader component. The saved build loader
version is used; a Minecraft prefix such as `1.7.10-` is removed where Prism's
component metadata expects the loader-only version. `LAUNCHER` and `MCIL`
packages are omitted because Prism installs the declared loader itself.

### MCIL or FileDirector bootstrap

Compatible releases are loaded from Modrinth only when the export window is
opened. The chosen downloader JAR is downloaded by solder.py, checked against
Modrinth's declared size, SHA-1, and SHA-512, and included under
`.minecraft/mods/`. The archive therefore does not depend on Modrinth to
install the bootstrap after it has been generated.

MCIL receives its normal `pack.mcinstance` beneath
`.minecraft/config/mcinstanceloader/`. Its configuration is always included
and can preserve MCIL advanced optional menus. Required legacy/configuration
ZIP contents that MCIL must bundle can still increase the archive size.

FileDirector receives either a bundled config beneath
`.minecraft/config/mod-director/` or a small hosted pointer. Hosted delivery
requires a published, non-private build and can pin this build or follow the
`latest` or `recommended` channel. FileDirector keeps the Prism archive small
because package ZIPs and JARs are downloaded after the instance starts.

Both downloaders support **Solder only** and **Hybrid**. Hybrid uses validated
Modrinth CDN URLs for exact mapped versions and Solder URLs for everything
else. The selectors only offer downloader families enabled in Settings and
versions compatible with the build. The built-in MCIL and FileDirector
projects currently provide Forge releases; use Self-contained for other
loaders or unsupported Minecraft versions.

### Self-contained fallback

Every selected client or both-sides Solder ZIP is read from the local
repository when possible, checked against its stored MD5, and safely unpacked
under `.minecraft/`. Server-only packages are omitted. Required ungrouped
packages are included, basic optional packages are left out, and packages in
advanced groups follow each group's saved default selection. This is one
concrete selection and does not present an optional-mod chooser after import.
Unsafe paths, symbolic links, oversized packages, checksum failures, and two
packages targeting the same file fail the export.

The self-contained ZIP contains the actual build files and does not require
MCIL, FileDirector, Packwiz, or a public hosted configuration after download.
If a package is not present in the local repository, solder.py falls back to
its configured public Solder repository while creating the export.

## Modloader ownership

New distribution exports do not copy Forge, Fabric, NeoForge, Quilt, or
LiteLoader binaries from a `LAUNCHER` package. MRPack declares the loader in
`dependencies`; a CurseForge pack declares Forge in `minecraft.modLoaders`;
Prism uses components in `mmc-pack.json`; and Packwiz and standalone MCIL
declare the saved or temporarily overridden loader version in their own
metadata. The target launcher installs it.

This does not change Technic compatibility. The normal Technic read API still
returns its legacy `forge` value, additive `modloader` metadata, and selected
`LAUNCHER` packages such as a legacy Solder ZIP containing `bin/modpack.jar`.

## Public routes and API-only mode

Packwiz, FileDirector, and Modpack Director routes are read-only and are available in
`API_ONLY=True` mode. Public routes expose only published builds. Hidden
modpacks remain directly addressable by slug, matching the Technic read API;
private or unpublished builds return `404`.

MCIL, CSV, MRPack, CurseForge, and Prism archives are authenticated
management-side downloads rather than permanent public archive URLs. An
MRPack or CurseForge archive may still contain a public Director pointer
served by an API-only process.

Generated Packwiz, FileDirector, and Modpack Director responses include a SHA-256 `ETag` and
`Cache-Control: public, no-cache`. Clients and reverse proxies can revalidate
metadata without keeping a stale channel result. JAR and Solder ZIP integrity
continues to use the stored MD5; SHA-256 here identifies generated manifest
content, not package artifacts.

## Troubleshooting

- **The export button is missing:** enable that format under **Settings > Env
  Settings**.
- **A hosted export is rejected:** publish the build and make it non-private.
- **A client receives a local path:** set `PUBLIC_REPO_LOCATION` to the public
  repository URL; keep local paths in `MD5_REPO_LOCATION` only.
- **A Director pointer has the wrong hostname:** set `APP_URL` to the public
  solder.py URL.
- **A mod is absent from Packwiz:** it needs a stored raw JAR MD5. Use **Create
  MCIL JAR** for a compatible legacy Solder ZIP.
- **An MCIL optional fails:** optional entries must be raw JARs, and MCIL 2.7
  cannot represent a server-only optional choice.
- **No MRPack or CurseForge downloader versions appear:** enable at least one
  compatible downloader. Modpack Director is available only in the CurseForge
  selector; CurseForge also requires its API key.
