# solder.py write API reference

The write API follows the current Technic Solder route layout and is extended
with solder.py's modloader, server, optional, dependency, Modrinth, Maven and
MCInstanceLoader fields. It is intended for the trusted management deployment,
not the public launcher-facing API instance.

## Enable and authenticate

Writes are disabled by default. Enable them with:

```ini
WRITE_API=True
```

After logging into the management interface, open **Settings → API Tokens** and
create the first token. Send it with every write request:

```http
Authorization: Bearer 12|TOKEN_SECRET
Content-Type: application/json
```

Only a SHA-256 digest is stored. The plaintext token is displayed once. A token
inherits its user's permissions and per-modpack access; it does not bypass
either. The token table and token format are compatible with current Technic
Solder personal-access tokens.

Tokens may also manage the authenticated user's own tokens:

| Method | Route | Action |
| --- | --- | --- |
| `GET` | `/api/token` | List tokens without their secrets. |
| `POST` | `/api/token` | Create a token using `{"name":"CI"}`. |
| `DELETE` | `/api/token/{id}` | Revoke one of the user's tokens. |

## Technic-compatible routes

| Method | Route | Permission | Action |
| --- | --- | --- | --- |
| `POST` | `/api/modpack` | `modpacks_create` | Create a modpack. |
| `POST` | `/api/modpack/{slug}/clone` | create + source manage/access | Clone a modpack and its builds. |
| `PUT` | `/api/modpack/{slug}` | `modpacks_manage` + access | Update a modpack. |
| `DELETE` | `/api/modpack/{slug}` | `modpacks_delete` + access | Delete a modpack and its builds. |
| `POST` | `/api/modpack/{slug}/build` | `modpacks_manage` + access | Create or clone a build. |
| `PUT` | `/api/modpack/{slug}/{version}` | `modpacks_manage` + access | Update a build. |
| `DELETE` | `/api/modpack/{slug}/{version}` | `modpacks_manage` + access | Delete a build. |
| `POST` | `/api/modpack/{slug}/{version}/mod` | `modpacks_manage` + access | Add a mod version. |
| `PUT` | `/api/modpack/{slug}/{version}/mod/{modSlug}` | `modpacks_manage` + access | Change its version or optional state. |
| `DELETE` | `/api/modpack/{slug}/{version}/mod/{modSlug}` | `modpacks_manage` + access | Remove a mod. |
| `POST` | `/api/mod` | `mods_create` | Create a mod. |
| `PUT` | `/api/mod/{slug}` | `mods_manage` | Update a mod. |
| `DELETE` | `/api/mod/{slug}` | `mods_delete` | Delete a mod and its versions. |
| `POST` | `/api/mod/{slug}/version` | `mods_manage` | Create a mod version. |
| `PUT` | `/api/mod/{slug}/{version}` | `mods_manage` | Update a mod version. |
| `DELETE` | `/api/mod/{slug}/{version}` | `mods_manage` | Delete an unused mod version. |
| `POST` | `/api/client` | `solder_clients` | Create a launcher client. |
| `PUT` | `/api/client/{uuid}` | `solder_clients` | Update its name or replace its modpack IDs. |
| `DELETE` | `/api/client/{uuid}` | `solder_clients` | Delete a client. |
| `POST` | `/api/minecraft/refresh` | authenticated | Compatibility no-op; solder.py uses free-form version fields. |

Create-build requests require `version` and `minecraft`. They may include
`forge`, `modloader`, `is_published`, `private`, `min_java`, `min_memory`,
`clone_from`, and `clone_from_modpack`. `min_java` is a free-form string and
accepts complete versions such as `1.8.0_51`.

Create-mod requests require `name` and `pretty_name`. They may include
`author`, `description`, `link`, private management `notes`, `side`, `modtype`,
and `dependencies`. Dependencies replace the mod's complete dependency list
when supplied and may contain mod IDs or slugs.

Create-version requests require `version` and a 32-character `md5`. They may
include `filesize`, `mcversion`, `modloader`, and the raw-JAR `jarmd5`. solder.py
does not use mod-version notes.

Adding a build mod uses the Technic fields `mod_slug` and `mod_version` and may
also include `optional`. Compatibility is enforced against the build's exact
Minecraft version and modloader. Missing declared dependencies are added using
the newest compatible local version.

Example:

```bash
curl -X POST https://solder.example.com/api/modpack/example/1.0/mod \
  -H "Authorization: Bearer 12|TOKEN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"mod_slug":"example-mod","mod_version":"1.20.1-2.0","optional":true}'
```

## solder.py integration routes

| Method | Route | Action |
| --- | --- | --- |
| `GET` | `/api/integration/modrinth/search?q={query}` | Search Modrinth. |
| `POST` | `/api/integration/modrinth/mod` | Link `project_id` without downloading releases. |
| `GET`, `POST` | `/api/integration/maven/repository` | List or create standard Maven repository roots. |
| `DELETE` | `/api/integration/maven/repository/{id}` | Delete a repository that has no configured artifacts. |
| `GET`, `POST` | `/api/integration/maven/artifact` | List or create Maven-managed mods. |
| `GET`, `PUT` | `/api/integration/maven/artifact/{id}` | Read the catalog or change its Minecraft mapping rule. |
| `POST` | `/api/integration/maven/artifact/{id}/refresh` | Refresh standard Maven metadata without downloading JARs. |
| `PUT` | `/api/integration/maven/artifact/{id}/version/{mappingId}` | Manually map or disable one upstream version. |
| `GET` | `/api/modpack/{slug}/{version}/mod/{modSlug}/integration-versions` | List compatible, unimported provider releases, including configured Maven artifacts. |
| `POST` | `/api/mod/{slug}/{version}/mcil-jar` | Extract and verify a raw JAR for MCInstanceLoader. |

To add or update a provider-managed mod in a build, send
`integration_version_id` instead of `mod_version`. solder.py downloads,
verifies and packages that one release on demand before changing the build.

Creating a Maven artifact requires `repository_id`, `group_id`, `artifact_id`,
`title`, and `redistribution_confirmed: true`. The Solder slug is generated
from the mod title followed by the configured repository name; a submitted
`slug` is ignored for compatibility with older clients. The route also accepts
`classifier`, `extension` (`jar`), `author`, `description`, `link`, `side`,
`modloader`, and one of these version mapping configurations:

```json
{"version_mode":"EMBEDDED","version_pattern":"{minecraft}-{version}"}
```

```json
{"version_mode":"FIXED","fixed_minecraft":"1.7.10"}
```

```json
{"version_mode":"MANUAL"}
```

Manual version updates accept `minecraft`, `mod_version`, `modloader`, and
`enabled`. An enabled mapping requires both version strings. Maven repository
creation requires `solder_env`; artifact creation requires `mods_create`;
catalog refreshes and mapping changes require `mods_manage`.

## Responses and errors

Successful creates return `201`; updates and deletes return `200`. Validation
errors return `422`, permission failures return `403`, missing resources return
`404`, and absent or invalid bearer tokens return `401`. Errors use the normal
Solder JSON form:

```json
{"error":"Description"}
```
