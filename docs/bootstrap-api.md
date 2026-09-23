# Dedicated Solder bootstrap API

This guide defines the read-only API contract for a Solder-aware bootstrap
mod. It is intended for a small loader component that downloads and maintains
the packages in a solder.py build, including advanced optional groups. It does
not replace the Technic API: existing launchers continue to use the ordinary
build manifest unchanged.

The endpoint is available in API-only deployments and does not require the
write API.

## Reference client

[SolderPy Modpack Loader](https://github.com/Thorfusion/solderpy_loader) implements
this contract and presents basic and advanced optional choices before mod
discovery. Its distribution projects are Modrinth `5LpwENAj` and CurseForge
`1702825`. SolderPy Modpack Loader requires Relauncher, published as Modrinth
`zCFNaupz` and CurseForge `1491728`.

SolderPy Modpack Loader defaults its local `target` to `auto`. Relauncher detects the
active client or dedicated-server side before restarting the JVM, allowing the
loader to request the matching `target=client` or `target=server` manifest.
Administrators can set an explicit target as an override.

## Discover and request the manifest

First request `GET /api/`. A compatible server reports
`bootstrap_manifest: true` and its `bootstrap_schema` version.

Fetch a resolved build with:

```http
GET /api/modpack/{slug}/{build}/bootstrap
GET /api/modpack/{slug}/recommended/bootstrap
GET /api/modpack/{slug}/latest/bootstrap
```

The route accepts these query arguments:

| Argument | Values | Default | Meaning |
| --- | --- | --- | --- |
| `target` | `client`, `server` | `client` | Filter packages by their configured side. |
| `source` | `hybrid`, `solder` | `hybrid` | Choose native-platform ownership (`hybrid`) or Loader-owned build packages (`solder`). Both retain the same verified JAR URL fallbacks. |
| `platform` | `modrinth`, `curseforge`, `prism`, `technic` | none | Identify the native platform that installed SolderPy Modpack Loader and may own package files. |
| `ownership` | `server`, `explicit` | `server` | Generated exports use `explicit`; it keeps the complete graph Loader-owned until the exported Loader config applies its exact native membership list. |
| `from` | Build version, `recommended`, or `latest` | none | Add changes from an installed build. |
| `cid` | Client UUID | none | Read a private modpack associated with that client. |
| `k` | Solder API key | none | Privileged read access; do not distribute this secret in a client mod. |

Use the exact endpoint above. The virtual Technic suffixes such as
`-optional` are not needed because the bootstrap response always contains all
three optional states.

## Manifest shape

This abbreviated example is a complete, valid schema-version 1 response:

```json
{
  "schema": "solder.py/bootstrap",
  "schema_version": 1,
  "modpack": {
    "id": 7,
    "slug": "example-pack",
    "name": "Example Pack"
  },
  "build": {
    "id": 12,
    "version": "2.1",
    "minecraft": "1.20.1",
    "modloader": "FORGE",
    "modloader_version": "47.3.0",
    "java": "17",
    "java_runtime": "java-runtime-gamma",
    "memory": 4096
  },
  "target": "client",
  "source": "hybrid",
  "update_policy": {
    "remove_unlisted_mod_files": false
  },
  "optional_mode": {
    "id": 1,
    "name": "advanced"
  },
  "selection_policy": {
    "states": {
      "0": "required",
      "1": "optional",
      "2": "excluded"
    },
    "ignored_modtypes": ["BOOTSTRAP", "LAUNCHER", "MCIL"],
    "required_memberships": [91],
    "default_memberships": [91, 92]
  },
  "groups": [
    {
      "id": 5,
      "key": "World generation",
      "name": "World generation",
      "description": "Choose one preset.",
      "selection_type": "single",
      "selection_type_id": 1,
      "minimum": 1,
      "maximum": 1,
      "sort_order": 10,
      "choices": [
        {
          "membership_id": 92,
          "modversion_id": 32,
          "slug": "standard-world",
          "name": "Standard World",
          "version": "1.0",
          "state": 2,
          "state_name": "excluded",
          "selected_by_default": true,
          "sort_order": 1
        }
      ]
    }
  ],
  "packages": [
    {
      "id": 31,
      "membership_id": 91,
      "name": "example-library",
      "pretty_name": "Example Library",
      "author": "Example Author",
      "description": "A required library.",
      "link": "https://example.com/project",
      "version": "1.20.1-4.0",
      "minecraft": "1.20.1",
      "minecraft_versions": ["1.20.1"],
      "modloader": "FORGE",
      "modloaders": ["FORGE"],
      "side": "BOTH",
      "type": "MOD",
      "modtype": "MOD",
      "install_owner": "loader",
      "bootstrap_managed": true,
      "enforce": true,
      "url": "https://cdn.example.com/mods/example-library/example-library-1.20.1-4.0.jar",
      "md5": "0123456789abcdef0123456789abcdef",
      "filesize": 24680,
      "download": {
        "url": "https://cdn.example.com/mods/example-library/example-library-1.20.1-4.0.jar",
        "md5": "0123456789abcdef0123456789abcdef",
        "filesize": 24680,
        "format": "jar",
        "path": "mods/example-library-1.20.1-4.0.jar",
        "sources": [
          {
            "provider": "modrinth",
            "url": "https://cdn.modrinth.com/data/project/version/upstream-name.jar"
          },
          {
            "provider": "solder",
            "url": "https://cdn.example.com/mods/example-library/example-library-1.20.1-4.0.jar"
          }
        ]
      },
      "optional": false,
      "selection": {
        "state": 0,
        "state_name": "required",
        "group_id": null,
        "group_key": null,
        "selected_by_default": true,
        "sort_order": 0
      },
      "dependencies": []
    },
    {
      "id": 32,
      "membership_id": 92,
      "name": "standard-world",
      "pretty_name": "Standard World",
      "author": "Example Author",
      "description": "The standard world preset.",
      "link": "https://example.com/world",
      "version": "1.0",
      "minecraft": "1.20.1",
      "minecraft_versions": ["1.20.1"],
      "modloader": "FORGE",
      "modloaders": ["FORGE"],
      "side": "CLIENT",
      "type": "CONFIG",
      "modtype": "CONFIG",
      "install_owner": "loader",
      "bootstrap_managed": true,
      "enforce": false,
      "url": "https://cdn.example.com/mods/standard-world/standard-world-1.0.zip",
      "md5": "fedcba9876543210fedcba9876543210",
      "filesize": 6543,
      "download": {
        "url": "https://cdn.example.com/mods/standard-world/standard-world-1.0.zip",
        "md5": "fedcba9876543210fedcba9876543210",
        "filesize": 6543,
        "format": "solder_zip",
        "extract_to": "."
      },
      "optional": false,
      "selection": {
        "state": 2,
        "state_name": "excluded",
        "group_id": 5,
        "group_key": "World generation",
        "selected_by_default": true,
        "sort_order": 1
      },
      "dependencies": [
        {
          "id": 8,
          "name": "example-library",
          "pretty_name": "Example Library",
          "side": "BOTH",
          "modtype": "MOD",
          "required": true,
          "present": true,
          "membership_id": 91,
          "version": "1.20.1-4.0"
        }
      ]
    }
  ],
  "manifest_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

`manifest_hash` is a stable SHA-256 identifier for the resolved manifest. It
is not a package checksum. A `MOD` entry uses the verified raw-JAR MD5 stored
by solder.py. Other package types use the Solder ZIP MD5.

Unknown fields must be ignored. A client must reject an unknown
`schema_version` unless it explicitly supports that version.

## Advanced optional rules

The numeric package state is the Technic/basic truth and remains independent
from the advanced group default:

| State | Name | Basic behavior |
| --- | --- | --- |
| `0` | `required` | Install the package. |
| `1` | `optional` | Offer it, disabled by default. |
| `2` | `excluded` | Do not send it through the basic/Technic selection. It is available only through an advanced group. |

When `optional_mode.name` is `basic`, `groups` is empty. Start with
`selection_policy.default_memberships` and allow the user to toggle state `1`
packages.

When it is `advanced`:

1. Install every ungrouped membership in `required_memberships`.
2. Restore saved choices by group `key` and package `name` (slug), not by row
   IDs. IDs change when a build is cloned.
3. If no saved choice remains valid, use `selected_by_default`.
4. Enforce each group's `minimum` and `maximum`. A `single` group requires
   exactly one choice; a `multiple` group accepts zero or more.
5. Do not install an unselected grouped package even if its numeric state is
   `0`. That numeric state is its separate Technic/basic behavior.

Package dependencies are always required. A selected package's dependencies
must also be selected. `present: true` identifies the exact target-compatible
build membership. If `present` is false, the build cannot satisfy that
dependency for the requested target; stop with a useful error rather than
silently downloading some other version. If dependency closure conflicts with
a single-choice group, ask the user to choose a valid combination or fail in
headless mode.

## Package ownership and updates

`download.format: jar` means download the raw JAR, verify its byte size and
MD5, and store it at the relative `download.path`. The raw-JAR size is kept
separate from the Solder ZIP size. It may be `null` for a legacy row migrated
before solder.py verified or regenerated that JAR; in that case, enforce a
reasonable client download limit while streaming and still verify the MD5. A
`MOD` without a verified raw JAR falls back to its normal `solder_zip`
instruction, preserving legacy build compatibility while making the extra
extraction cost explicit.

A Loader-owned raw JAR may include an ordered `download.sources` list in
**either** source mode. A per-version HTTPS override is first, a saved
Modrinth or enabled direct Maven URL is second, and the Solder-hosted JAR is
last. SolderPy Modpack Loader retries the next source after a
transport, size, or MD5 failure. Every source represents the same bytes and
therefore uses the one parent `download.md5` and `download.filesize`; renaming
a file does not change either value. `download.url` remains the Solder-hosted
URL, so clients that do not understand `sources` retain the existing behavior.
If solder.py cannot use saved native-provider metadata, it still returns the
override, when configured, and the Solder URL. `source=solder` means the
platform is not asked to install ordinary build packages natively; it does
**not** mean their bytes must come from Solder hosting. `source=hybrid` lets
the platform install exact native matches and leaves the rest to the Loader.
The response always retains the complete dependency graph. Each
package has an `install_owner` of `loader`, `launcher`, or `ignored`, with the
legacy `bootstrap_managed` boolean mirroring whether that value is `loader`.
Launcher-owned dependencies therefore remain visible and can satisfy closure
without being downloaded twice.

Generated Modrinth and CurseForge exports request `ownership=explicit` and put
the exact memberships actually embedded in the native pack in the Loader
configuration's `launcherOwnedMemberships`. This is intentionally based on the
finished export plan, not merely provider metadata: if native resolution falls
back to Solder, the package remains Loader-owned. Advanced-group choices always
remain Loader-owned because native manifests cannot enforce their selection
rules. `platform=technic` is server-owned: Technic owns ungrouped required
packages only when that build uses Technic delivery, while SolderPy Modpack Loader owns
optional and advanced content.

`enforce` is a mod-wide SolderPy Modpack Loader package
policy and defaults to `true`. Other downloaders and export formats ignore it.
It applies to every version of that mod and to both JAR and ZIP downloads.
When true, SolderPy Modpack Loader retains its normal behavior: it verifies the
package's owned files on every launch and repairs missing or modified outputs.
When false, the Loader should trust an existing receipt without inspecting or
repairing those outputs while the package version, artifact MD5, and install
target remain unchanged. Users and mods may therefore change or remove the
installed files between package updates. Initial installation and a change to
the package version, artifact, or install target still install the package
normally and reset its managed files to the new package. This exception is
mainly useful for user-editable config packs. It does not weaken path
validation, size checks, MD5 verification, transaction safety, or ownership
checks when the package is installed or updated.

The field is additive to schema version 1. Older SolderPy Modpack Loader
versions that do not recognize it retain their existing launch-enforcement
behavior.
The installed loader must support the field before administrators can rely on
the unchanged-launch exception.

`update_policy.remove_unlisted_mod_files` is a modpack-wide SolderPy Modpack
Loader setting and defaults to `false`, which preserves the current behavior.
When it is true and the installed build changes, reconcile the instance's
`mods/` directory against the resolved packages and the user's optional
selections. Files that do not belong to the new resolved mod list are removed.
The cleanup must remain inside `mods/`, must not follow links outside the
instance, and must preserve the Loader itself, its runtime dependencies, and
launcher-provided modloader files. It does not clean configs, resource packs,
or any other instance directory. Stage or quarantine removals until all new
downloads have passed their size and MD5 checks so a failed update can roll
back safely. Other downloaders and export formats ignore this policy.

This field is also additive to schema version 1. Older SolderPy Modpack Loader
versions ignore it and keep unlisted mod files.

`download.format: solder_zip` is used by `CONFIG`, `RES`, `NONE`, and other
non-mod content. Download the archive, verify its byte size when supplied,
verify its MD5, and extract its contents relative to `download.extract_to`.
A robust bootstrap implementation should:

1. Download into a temporary file on the same filesystem.
2. Verify size and MD5 before extraction.
3. Reject absolute paths, `..` traversal, unsafe links, duplicate output paths,
   and unreasonably large expanded archives.
4. Record every extracted path under the package slug and version.
5. Replace files atomically where possible.
6. Remove an old file only when the receipt says that package owned it and no
   selected package in the new manifest owns it.
7. Write the new manifest, selections, and receipts only after the update
   succeeds.

The bootstrap must not load newly downloaded mod JARs into a Minecraft process
that has already completed loader discovery. Run before discovery, or stage
the update and require one controlled restart.

Packages with `install_owner: launcher` or `install_owner: ignored` (and thus
`bootstrap_managed: false`) must not be extracted by this client. When a
package changes from Loader-owned to launcher-owned, discard the Loader receipt
without deleting the launcher's file. For ordinary removals, delete a former
Loader output only if its current hash still matches the receipt; preserve
locally modified or unverifiable files. Ignored ownership covers `LAUNCHER` (the legacy Technic
modloader package) and `BOOTSTRAP` (an initial downloader/bootstrap package).
Store a dedicated Solder bootstrap package as type `BOOTSTRAP` if it is also
represented in the build; that prevents it from attempting to update itself.
The launcher or export format remains responsible for installing the modloader
and initial bootstrap component. Existing `MCIL` database values are migrated
to `BOOTSTRAP`.

## Incremental checks and channels

Persist the successfully installed build version. On the next check request:

```http
GET /api/modpack/example-pack/recommended/bootstrap?from=2.0
```

The response adds `changes.added`, `changes.updated`, `changes.removed`,
`changes.from`, `changes.to`, `changes.from_manifest_hash`, and
`changes.selection_changed`, and `changes.update_policy_changed`. The full
`packages`, `groups`, and `update_policy` values remain authoritative;
`changes` is an optimization and UI summary, not a replacement for
reconciliation.

Send the returned `ETag` in `If-None-Match` on later identical requests. A
`304 Not Modified` means the locally stored manifest may be reused. Responses
using private credentials are marked `private, no-cache`; this allows local
revalidation without permitting a shared cache to expose them.

For a channel such as `recommended`, always store the resolved
`build.version`, not the word `recommended`.

## Bootstrap configuration

The initial launcher/export should provide only non-secret configuration, for
example:

```json
{
  "api": "https://solder.example.com/api/",
  "modpack": "example-pack",
  "build": "recommended",
  "target": "client",
  "source": "hybrid",
  "platform": "modrinth",
  "launcherOwnedMemberships": [91]
}
```

Normalize the API URL once, require HTTPS outside local development, and derive
only the documented same-origin endpoint from it. Do not place a Solder `k`
value in a public pack: it grants broader repository read access and can be
recovered from the client. Private packs should use a per-client `cid` assigned
by Solder, or an administrator-designed short-lived credential exchange.

## Compatibility checklist

A bootstrap mod is compatible with schema version 1 when it:

- discovers the capability instead of changing the ordinary Technic request;
- supports states `0`, `1`, and `2` and named single/multiple groups;
- keys saved choices by group key and package slug;
- closes required dependencies before downloading;
- honors `target`, `source`, `platform`, side filtering, `install_owner`, and the export's exact native membership list;
- honors the mod-wide `enforce` package policy;
- honors `update_policy.remove_unlisted_mod_files` without deleting outside the instance's `mods/` directory or removing launcher-owned files;
- verifies each ZIP's size and MD5 and extracts it safely;
- tracks extracted-file ownership for reliable removal and rollback;
- handles ETags, resolved channels, and the optional `changes` summary;
- leaves modloader and initial bootstrap installation to the launcher/export.

For the rest of the read endpoints and visibility behavior, see the
[read API reference](api.md).
