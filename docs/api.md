# solder.py API reference

This document describes the public read API exposed by solder.py.
The standard routes and their default responses remain compatible with Technic
Solder. solder.py-specific behavior is additive and uses query arguments or
additional JSON fields.

## Base URL and format

All endpoints are relative to the solder.py installation URL and return JSON.
For example:

```text
https://solder.example.com/api/modpack/example-pack/1.0
```

The optional authenticated write routes are documented separately in the
[write API reference](write-api.md). Unknown API routes and unsupported methods
return JSON rather than the management interface's HTML error page.

Packwiz and FileDirector are public file formats rather
than Technic JSON API routes. Authenticated build management can also create Modrinth and CurseForge
archives that bootstrap their non-native files from Solder. Their paths,
visibility rules, and enable switches are covered in the
[distribution-format guide](distribution-formats.md). Public Director
bootstrap files are registered on the read-only application and remain
available when `API_ONLY=True`.

Values placed in a path or query string must be URL encoded.

## Authentication and visibility

The read API supports the Technic-style credentials below on modpack, build
and mod-version requests.

| Argument | Meaning |
| --- | --- |
| `cid` | A launcher/client UUID. It grants access to private modpacks associated with that client. |
| `k` | A configured Solder API key. A valid key grants read access to private modpacks and private published builds. |

When `WRITE_API=True`, a personal token can also be sent in the
`Authorization: Bearer {id}|{secret}` header. It grants read access to the
modpacks assigned to that token's user, matching current Technic Solder.
Users with `solder_full` access can read every published build. Invalid bearer
credentials do not make otherwise-public read requests fail; they simply add
no private access.

If both are supplied and `k` is valid, API-key access is used.

Visibility is enforced as follows:

| Resource | Anonymous | Associated `cid` | Valid `k` |
| --- | --- | --- | --- |
| Public, visible modpack | Listed and readable | Listed and readable | Listed and readable |
| Public, hidden modpack | Not listed, readable directly by slug | Readable directly | Listed and readable |
| Private modpack | Not readable | Readable when associated | Readable |
| Public published build | Readable when its pack is readable | Readable | Readable |
| Private published build | Not readable | Readable when its pack is associated | Readable |
| Unpublished build | Not readable | Not readable | Not readable |

An inaccessible private resource returns the same `404` response as a resource
that does not exist.

The write API is disabled by default. `API_ONLY=True` can therefore be used on
a public instance backed by a read-only database account as long as
`WRITE_API` remains disabled.

## Discover API capabilities

```http
GET /api/
```

Example response:

```json
{
  "api": "solder.py",
  "version": "v1.10.1",
  "stream": "DEV",
  "capabilities": {
    "advanced_optionals": true,
    "bootstrap_manifest": true,
    "bootstrap_schema": 1,
    "build_channels": true,
    "build_comparison": true,
    "optional_manifests": true,
    "server_manifests": true,
    "write_api": false
  }
}
```

Clients should use the capability flags rather than assuming every Solder
installation supports solder.py extensions.

## Verify an API key

```http
GET /api/verify
GET /api/verify/{key}
```

Calling the route without a key returns:

```json
{"error": "No API key provided."}
```

A valid key returns:

```json
{
  "valid": "Key validated.",
  "name": "Deployment key",
  "created_at": "1970-01-01T00:00:00+00:00"
}
```

An invalid key returns `{"error":"Invalid key provided."}`. These verification
responses currently use HTTP `200`, including the error responses. The
`created_at` value is a compatibility placeholder and should not be interpreted
as the key's real creation time.

## List modpacks

```http
GET /api/modpack
GET /api/modpack?cid={client_uuid}
GET /api/modpack?k={api_key}
GET /api/modpack?include=full
```

The default response maps each accessible modpack slug to its display name:

```json
{
  "modpacks": {
    "example-pack": "Example Pack"
  },
  "mirror_url": "https://cdn.example.com/mods/"
}
```

`include=full` replaces each display-name value with the complete modpack
object documented in the next section.

## Get a modpack

```http
GET /api/modpack/{slug}
```

The route accepts `cid` and `k`.

Example response:

```json
{
  "id": 7,
  "name": "example-pack",
  "display_name": "Example Pack",
  "recommended": "2.0",
  "latest": "2.1",
  "capabilities": {
    "advanced_optionals": true,
    "bootstrap_manifest": true,
    "optional": true,
    "server": true
  },
  "builds": [
    "2.0",
    "2.0-optional",
    "2.0-server",
    "2.1",
    "2.1-optional",
    "2.1-server"
  ]
}
```

`name` is the URL slug; `display_name` is the human-readable name. Only
accessible, published builds are returned. The `-optional` and `-server`
entries are virtual aliases generated for packs that enable those capabilities.

## Get a build manifest

```http
GET /api/modpack/{slug}/{build}
```

The route accepts the following arguments in addition to `cid` and `k`:

| Argument | Values | Default | Meaning |
| --- | --- | --- | --- |
| `include` | `mods` | none | Add descriptive and solder.py metadata to each mod entry. |
| `target` | `client`, `server` | `client` | Select the machine for which the manifest is being produced. |
| `optional` | Boolean | `false` | Include optional packages compatible with the selected target. |
| `from` | Build version, `recommended`, or `latest` | none | Include a package-level comparison from another accessible build. |

Boolean arguments accept `true`, `false`, `1`, `0`, `yes`, `no`, `on`, and
`off`, case-insensitively.

### Standard Technic manifest

With no solder.py extension arguments, the route returns the ordinary Technic
manifest shape:

```json
{
  "id": 12,
  "minecraft": "1.20.1",
  "java": "1.8.0_51",
  "java_runtime": "jre-legacy",
  "memory": 4096,
  "forge": "47.3.0",
  "mods": [
    {
      "id": 31,
      "name": "example-mod",
      "version": "1.20.1-4.0",
      "md5": "0123456789abcdef0123456789abcdef",
      "filesize": 123456,
      "url": "https://cdn.example.com/mods/example-mod/example-mod-1.20.1-4.0.zip"
    }
  ]
}
```

The default target is the client and optional packages are excluded. The mods
are returned in deterministic natural-name order. `java` is a free-form string,
so complete Java versions such as `1.8.0_51` are preserved unchanged.

When a public build explicitly enables SolderPy Loader for Technic, solder.py
adds one internal `solderpy-loader-bootstrap` entry containing SolderPy Loader,
Relauncher, and the build configuration. `LAUNCHER` and `BOOTSTRAP` entries
remain in the Technic response; every other package is supplied by the
dedicated bootstrap API before mod discovery. If that integration becomes
inactive, ordinary Technic manifest delivery applies again. Activating it also
disables the modpack's legacy optional shadow build so Technic receives only
one optional-delivery mechanism.

`java_runtime` is the nullable per-build Mojang runtime override supported by
current Technic Launcher releases. Accepted component names are `jre-legacy`
(Java 8), `java-runtime-alpha` (Java 16), `java-runtime-beta` and
`java-runtime-gamma` (Java 17), `java-runtime-delta` (Java 21), and
`java-runtime-epsilon` (Java 25). `null` leaves runtime selection automatic.
The component selects a Mojang-managed runtime family, not a particular patch
release. In particular, `jre-legacy` does not guarantee a current Java 8 update;
use the separate `java` requirement and a manually installed runtime when a
specific minimum Java 8 update is required.

### Expanded mod metadata

`include=mods` adds these fields to every entry:

```json
{
  "pretty_name": "Example Mod",
  "author": "Example Author",
  "description": "Example description",
  "link": "https://example.com/project",
  "side": "BOTH",
  "type": "MOD",
  "modtype": "MOD",
  "modloader": "FORGE",
  "optional": false,
  "dependencies": [
    {
      "id": 8,
      "name": "example-library",
      "pretty_name": "Example Library",
      "side": "BOTH",
      "modtype": "MOD"
    }
  ]
}
```

`type` and `modtype` contain the same value. `type` is retained as a
compatibility alias. `dependencies` contains the mod's directly declared
dependencies. The final `mods` array remains authoritative: dependencies that
were resolved into the build are present there as ordinary packages, so a
launcher or server updater should not download packages solely from the
dependency metadata.

### Client, server and optional filtering

Examples:

```http
GET /api/modpack/example-pack/2.1?target=client
GET /api/modpack/example-pack/2.1?optional=true
GET /api/modpack/example-pack/2.1?target=server
GET /api/modpack/example-pack/2.1?target=server&optional=true
```

The filters behave as follows:

| Request | Included sides | Optional packages |
| --- | --- | --- |
| Default or `target=client` | `CLIENT`, `BOTH` | Excluded |
| `target=client&optional=true` | `CLIENT`, `BOTH` | Included |
| `target=server` | `SERVER`, `BOTH` | Excluded |
| `target=server&optional=true` | `SERVER`, `BOTH` | Included |

Server and optional requests return `404` when that capability is disabled for
the modpack.

The existing virtual suffixes remain supported:

```text
2.1-optional  ==  2.1?target=client&optional=true
2.1-server    ==  2.1?target=server
```

An actual build whose version ends in `-optional` or `-server` takes precedence
over the virtual suffix. Conflicting suffixes and arguments return `400`.

### Build channels

`recommended` and `latest` can be used in place of a build version:

```http
GET /api/modpack/example-pack/recommended?target=server
GET /api/modpack/example-pack/latest?target=server
```

An actual build named `recommended` or `latest` takes precedence. Otherwise the
name resolves to the modpack's configured channel version.

Using `target`, `optional`, `from`, `recommended`, or `latest` produces an
extended manifest. In addition to the normal fields, it contains:

```json
{
  "modpack": "example-pack",
  "version": "2.1",
  "modloader": "FORGE",
  "target": "server",
  "optional": false,
  "manifest_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

The actual resolved build is always reported in `version`. `manifest_hash` is a
stable SHA-256 digest of the resolved target manifest, and is also returned in
the HTTP `ETag` header.

### Compare builds for a server update

Pass the server's installed build in `from`:

```http
GET /api/modpack/example-pack/latest?target=server&from=2.0
```

The target response gains a `changes` object:

```json
{
  "changes": {
    "from": "2.0",
    "to": "2.1",
    "added": [
      {"name": "new-mod", "version": "1.0"}
    ],
    "updated": [
      {
        "from": {"name": "changed-mod", "version": "1.0"},
        "to": {"name": "changed-mod", "version": "2.0"}
      }
    ],
    "removed": [
      {"name": "old-mod", "version": "1.0"}
    ]
  }
}
```

The objects in the real response contain the complete extended mod fields, not
only the abbreviated fields shown above. A mod is considered updated when its
version or MD5 changes.

This is a package-level comparison. Because Solder archives can contain more
than one extracted file, server software must track which files belong to each
installed package if it wants to remove obsolete extracted files safely.

The manifest hash identifies the target manifest and does not include the
optional `changes` block.

## Get a dedicated bootstrap manifest

```http
GET /api/modpack/{slug}/{build}/bootstrap
GET /api/modpack/{slug}/recommended/bootstrap?from={installed_build}
GET /api/modpack/{slug}/latest/bootstrap?target=server
```

This additive solder.py endpoint is intended for a dedicated Solder-aware
bootstrap mod. Unlike the Technic manifest, it returns every target-compatible
package state (`0` required, `1` optional, and `2` excluded), named advanced
optional groups, defaults, dependencies, stable download instructions, and
whether the bootstrap should manage each package. It always returns the source
build data, even when the normal Technic response delegates delivery to
SolderPy Loader.

`MOD` packages prefer their canonical raw `.jar` repository URL and verified
JAR MD5. Their download instruction uses `format: "jar"` and supplies the
target path under `mods/`. If a legacy mod has no verified raw JAR, its normal
Solder ZIP is returned instead so the build remains usable, at the cost of ZIP
extraction during bootstrap. Non-mod content continues to use its Solder ZIP.

The route supports `cid`, `k`, `target`, and `from`, as documented in the
[dedicated bootstrap API guide](bootstrap-api.md). It returns an `ETag` and
honors `If-None-Match`. Clients must discover `bootstrap_manifest` and support
the reported `bootstrap_schema` before using it. The ordinary
`/api/modpack/{slug}/{build}` response remains unchanged for Technic clients.

## List mods

```http
GET /api/mod
```

Example response:

```json
{
  "mods": {
    "example-mod": "Example Mod"
  }
}
```

## Get a mod

```http
GET /api/mod/{name}
```

Example response:

```json
{
  "id": 4,
  "name": "example-mod",
  "pretty_name": "Example Mod",
  "author": "Example Author",
  "description": "Example description",
  "link": "https://example.com/project",
  "side": "BOTH",
  "type": "MOD",
  "modtype": "MOD",
  "modloader": "FORGE",
  "versions": ["1.20.1-3.0", "1.20.1-4.0"],
  "dependencies": [
    {
      "id": 8,
      "name": "example-library",
      "pretty_name": "Example Library",
      "side": "BOTH",
      "modtype": "MOD"
    }
  ]
}
```

Private mod management notes are never returned by the read API.

## Get a mod version

```http
GET /api/mod/{name}/{version}
```

The route accepts `cid` and `k`; these arguments determine which build
memberships may be disclosed.

Example response:

```json
{
  "id": 31,
  "mod_id": 4,
  "version": "1.20.1-4.0",
  "md5": "0123456789abcdef0123456789abcdef",
  "filesize": 123456,
  "url": "https://cdn.example.com/mods/example-mod/example-mod-1.20.1-4.0.zip",
  "side": "BOTH",
  "type": "MOD",
  "modtype": "MOD",
  "dependencies": [],
  "builds": [
    {
      "id": 12,
      "version": "2.1",
      "optional": false,
      "modpack": {
        "id": 7,
        "name": "example-pack",
        "display_name": "Example Pack"
      }
    }
  ]
}
```

`optional` belongs to the relationship between this mod version and that build;
it is not a global property of a mod or mod version.

`modloader` is an uppercase loader identifier such as `FORGE`, `NEOFORGE`,
`FABRIC`, `QUILT`, or `LITELOADER`. A `null` mod-version value means the
package is loader-agnostic. The field is included in expanded/extended build
manifests and direct mod-version responses; the default Technic manifest shape
is unchanged.

## Metadata values

A mod version may target more than one Minecraft version or modloader. A
single Minecraft version remains in the legacy-compatible
`minecraft`/`mcversion` field; a multi-version row uses the stable value
`MULTI`. Read `minecraft_versions` for the concrete compatibility list.
`modloader` retains its comma-separated compatibility string and
`modloaders` exposes the same values as an array. A null/empty set remains
universal.

`side` is one of:

- `CLIENT`
- `SERVER`
- `BOTH`

`modtype`/`type` is one of:

- `MOD`
- `LAUNCHER`
- `RES`
- `CONFIG`
- `BOOTSTRAP`
- `NONE`

`LAUNCHER` is the legacy Technic modloader/bootstrap package type. Its stored
and API value remains `LAUNCHER` for Technic compatibility. These entries remain
ordinary downloadable entries in the default Technic manifest. For legacy
packs such as Minecraft 1.7.10, this is commonly the Solder ZIP containing
`bin/modpack.jar` or `bin/version.json`; Technic Launcher downloads it from the
entry's repository URL. The build-level `forge` value declares the loader
version alongside it. MRPack and CurseForge archive exports intentionally
exclude this Technic-specific package because those launchers install their
declared loader.

`BOOTSTRAP` identifies an initial downloader/bootstrap package that must be
installed by the launcher and must not replace itself through the bootstrap
API. Existing database values named `MCIL` are migrated to `BOOTSTRAP`; MCIL
remains the name of the MCInstance Loader export format, not a package type.

## Errors

API errors have the form:

```json
{"error": "Description"}
```

Common status codes:

| Status | Meaning |
| --- | --- |
| `200` | Successful request. The key-verification routes also currently use this for an invalid or omitted key. |
| `400` | Invalid `target`/`optional` value or conflicting build variant arguments. |
| `404` | Resource absent or inaccessible, comparison build absent, or requested pack capability disabled. |
| `405` | HTTP method is not supported by the route. |
| `422` | Stored data cannot be represented by the bootstrap manifest contract. |

## Caching

Modpack, build and mod responses are cached in each solder.py process using the
configured `CACHE_TTL` and `CACHE_SIZE`. All query arguments are part of the
cache key, so client, server, optional and credentialed responses cannot share a
cache entry accidentally.

Extended manifests include a stable digest and `ETag`, allowing consumers to
detect unchanged content. The dedicated bootstrap endpoint honors
`If-None-Match` and may return `304 Not Modified`; other read routes do not
currently promise conditional responses, so clients should be prepared to
receive their JSON body each time.

## Suggested server update flow

1. Read `/api/` and confirm `server_manifests` is supported.
2. Read `/api/modpack/{slug}` and confirm the pack's `server` capability.
3. Request `/api/modpack/{slug}/recommended?target=server`.
4. Persist the resolved `version`, `manifest_hash`, and installed package/file
   ownership after a successful update.
5. On a later check, request the desired channel with
   `target=server&from={installed_version}`.
6. Download packages from the target manifest, validate their `filesize` and
   required Technic MD5, and apply the reported package changes.

MD5 is part of the Technic Solder compatibility contract and detects accidental
file changes. It is not a signature or proof that a download is trustworthy;
server software should still use HTTPS and a trusted repository origin.
