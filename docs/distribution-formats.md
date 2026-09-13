# Packwiz and FileDirector exports

solder.py can expose a published build as Packwiz metadata or as FileDirector
configuration files. The generated metadata is small and is served by the
solder.py Gunicorn process. Mod archives and JARs continue to download from
`PUBLIC_REPO_LOCATION`.

Both formats are disabled by default. In the management interface, open
**Settings > Env Settings**, enable the formats you want, and save. The switches
are stored in the database, so a separate process started with `API_ONLY=True`
uses the same settings and serves the same public files without exposing the
management interface.

Only public, published builds are exported. Hidden modpacks remain directly
addressable by slug, as they are in the Technic read API. Private modpacks,
private builds, and unpublished builds return `404`.

`PUBLIC_REPO_LOCATION` must be an HTTP or HTTPS URL accessible to players. A
local path is still valid for `MD5_REPO_LOCATION`, but it cannot be placed in a
client download manifest. `APP_URL` must be the public solder.py URL for
FileDirector `.remote.json` pointers; this deliberately avoids deriving a
download location from an untrusted request host.

## Packwiz

Use an exact build version or the `latest`/`recommended` channel:

```text
https://solder.example.com/packwiz/example-pack/1.0/pack.toml
https://solder.example.com/packwiz/example-pack/latest/pack.toml
https://solder.example.com/packwiz/example-pack/recommended/pack.toml
```

Channel URLs redirect to the resolved exact version. This keeps `pack.toml`,
`index.toml`, and every `.pw.toml` file on one exact build while Packwiz
verifies their SHA-256 hashes.

The generated layout is:

```text
pack.toml
index.toml
mods/{solder-mod-slug}.pw.toml
```

Each raw JAR uses the MD5 already verified and stored by solder.py. Side and
optional metadata are preserved; optional mods default to not selected. When a
build selects a modloader, its version must also be set so Packwiz can install
the same loader.

Packwiz cannot extract a Solder package ZIP as part of its standard metadata
format. Consequently, its index contains `MOD` packages that have a stored raw
JAR hash. `CONFIG`, `RES`, `NONE`, legacy mods without a raw JAR, `MCIL`, and
`LAUNCHER` packages remain in the Technic build but are omitted from this view.
Responses report the omitted count in `X-Solder-Excluded-Packages`. Use the
**Create JAR** action on a legacy mod version if it should be available to
Packwiz; configuration overrides still need to be maintained in the Packwiz
pack itself.

See the upstream [Packwiz pack format reference](https://packwiz.infra.link/reference/pack-format/pack-toml/)
and [packwiz-installer guide](https://packwiz.infra.link/tutorials/installing/packwiz-installer/)
for client setup.

## FileDirector

The main bundle contains required and optional packages:

```text
https://solder.example.com/filedirector/example-pack/latest/mods.bundle.json
```

FileDirector side metadata is generated for `CLIENT` and `SERVER` entries;
`BOTH` entries are left unrestricted. Its native optional selection fields
preserve Solder's optional status and default those packages to not selected.
Raw-JAR-ready mods download as JARs and are checked against their stored MD5.
Other ordinary Solder package types download their Solder ZIP and use
FileDirector's extract-and-delete policy at the instance root. `MCIL` and
`LAUNCHER` packages are excluded because they are launcher bootstrap packages,
not runtime files. `required.bundle.json` and `optional.bundle.json` are also
available if a pack author deliberately wants the two subsets separately; do
not load them alongside `mods.bundle.json`.

For a small local pointer that follows a Solder channel, place this file where
FileDirector reads its configuration files:

```text
https://solder.example.com/filedirector/example-pack/latest/mods.remote.json
```

The remote file points to the corresponding `.bundle.json`. A single-line
version value is also available for FileDirector's `remoteVersion` setting:

```text
https://solder.example.com/filedirector/example-pack/latest/version.txt
```

Exact build versions and `recommended` work in all FileDirector paths as well.
See FileDirector's upstream documentation for
[bundle configs](https://github.com/TerraFirmaCraft-The-Final-Frontier/FileDirector/wiki/Config-Type:-Bundle),
[remote configs](https://github.com/TerraFirmaCraft-The-Final-Frontier/FileDirector/wiki/Config-Type:-Remote),
and [remote modpack versions](https://github.com/TerraFirmaCraft-The-Final-Frontier/FileDirector/wiki/Modpack).

## HTTP caching

Generated responses include a SHA-256 `ETag` and `Cache-Control: public,
no-cache`. Clients and reverse proxies can therefore revalidate without using a
stale channel response. Packwiz channel entry points redirect to exact versions;
FileDirector channel files resolve the current database value on each
revalidation.
