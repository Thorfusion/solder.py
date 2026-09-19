# Dedicated Solder bootstrap API

This guide defines the read-only API contract for a Solder-aware bootstrap
mod. It is intended for a small loader component that downloads and maintains
the packages in a solder.py build, including advanced optional groups. It does
not replace the Technic API: existing launchers continue to use the ordinary
build manifest unchanged.

The endpoint is available in API-only deployments and does not require the
write API.

## Reference client

[SolderPy Loader](https://github.com/Thorfusion/solderpy_loader) implements
this contract and presents basic and advanced optional choices before mod
discovery. Its distribution projects are Modrinth `5LpwENAj` and CurseForge
`1702825`. SolderPy Loader requires Relauncher, published as Modrinth
`zCFNaupz` and CurseForge `1491728`.

SolderPy Loader defaults its local `target` to `auto`. Relauncher detects the
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
    "ignored_modtypes": ["BOOTSTRAP", "LAUNCHER"],
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
      "bootstrap_managed": true,
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
      "bootstrap_managed": true,
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

A Modrinth-managed raw JAR may include an ordered `download.sources` list.
SolderPy Loader tries the native Modrinth file first and the Solder-hosted JAR
second. It retries the next source after a transport, size, or MD5 failure.
Every source represents the same bytes and therefore uses the one parent
`download.md5` and `download.filesize`; renaming a file does not change either
value. `download.url` remains the Solder-hosted URL, so clients that do not
understand `sources` retain the existing behavior. If solder.py cannot resolve
Modrinth while producing the manifest, it omits `sources` and still returns the
Solder URL.

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

Packages with `bootstrap_managed: false` are visible for diagnostics but must
not be extracted by this client. This covers `LAUNCHER` (the legacy Technic
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
`changes.selection_changed`. The full `packages` and `groups` arrays remain
authoritative; `changes` is an optimization and UI summary, not a replacement
for reconciliation.

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
  "target": "client"
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
- honors `target`, side filtering, and `bootstrap_managed`;
- verifies each ZIP's size and MD5 and extracts it safely;
- tracks extracted-file ownership for reliable removal and rollback;
- handles ETags, resolved channels, and the optional `changes` summary;
- leaves modloader and initial bootstrap installation to the launcher/export.

For the rest of the read endpoints and visibility behavior, see the
[read API reference](api.md).
