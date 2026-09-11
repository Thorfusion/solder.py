# solder.py API reference

This document describes the public, read-only HTTP API exposed by solder.py.
The standard routes and their default responses remain compatible with Technic
Solder. solder.py-specific behavior is additive and uses query arguments or
additional JSON fields.

## Base URL and format

All endpoints are relative to the solder.py installation URL and return JSON.
For example:

```text
https://solder.example.com/api/modpack/example-pack/1.0
```

All currently documented endpoints use `GET`. Unknown API routes and unsupported
methods return JSON rather than the management interface's HTML error page.

Values placed in a path or query string must be URL encoded.

## Authentication and visibility

The read API supports the two Technic-style credentials below. They are query
arguments and can be used on modpack, build and mod-version requests.

| Argument | Meaning |
| --- | --- |
| `cid` | A launcher/client UUID. It grants access to private modpacks associated with that client. |
| `k` | A configured Solder API key. A valid key grants read access to private modpacks and private published builds. |

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

There is no public write API. `API_ONLY=True` can therefore be used on a public
instance backed by a read-only database account.

## Discover API capabilities

```http
GET /api/
```

Example response:

```json
{
  "api": "solder.py",
  "version": "v1.7.4",
  "stream": "DEV",
  "capabilities": {
    "build_channels": true,
    "build_comparison": true,
    "optional_manifests": true,
    "server_manifests": true
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
  "java": "17",
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
are returned in deterministic natural-name order.

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

The internal management note is never returned by the read API.

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

`side` is one of:

- `CLIENT`
- `SERVER`
- `BOTH`

`modtype`/`type` is one of:

- `MOD`
- `LAUNCHER`
- `RES`
- `CONFIG`
- `MCIL`
- `NONE`

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

## Caching

Modpack, build and mod responses are cached in each solder.py process using the
configured `CACHE_TTL` and `CACHE_SIZE`. All query arguments are part of the
cache key, so client, server, optional and credentialed responses cannot share a
cache entry accidentally.

Extended manifests include a stable digest and `ETag`, allowing consumers to
detect unchanged content. The API does not currently promise conditional `304`
responses, so clients should be prepared to receive the JSON body each time.

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
