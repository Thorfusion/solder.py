![solder.py](https://files.thorfusion.com/images/solderwhite.py.png)

# What is solder?

>Technic Solder is an API that sits between a modpack repository and the launcher. It allows you to easily manage multiple modpacks in one single location. It's the same API we use to distribute our modpacks!
>
>Using Solder also means your packs will download each mod individually. This means the launcher can check MD5's against each version of a mod and if it hasn't changed, use the cached version of the mod instead. What does this mean? Small incremental updates to your modpack doesn't mean redownloading the whole thing every time!
>
>-- Technic

# About solder.py

solder.py is a Minecraft modpack management platform designed to remain
compatible with Technic Solder while adding easier management and distribution
options for other launchers. It began as an upload interface for the
Terralization modpack and grew into a complete standalone Solder implementation.

Administrators moving from the original project should start with
[solder.py for Technic Solder users](docs/technic-solder-users.md). The
[mod and modpack management guide](docs/management-guide.md) explains the
fields and day-to-day workflow in the management interface.

The complete read API, including Technic-compatible routes and solder.py server
and optional-manifest extensions, is documented in the
[API reference](docs/api.md).
Developers building a Solder-aware bootstrap mod should also use the
[dedicated bootstrap API guide](docs/bootstrap-api.md), which covers advanced
optional groups, dependency resolution, updates, and safe archive ownership.

Management-side Modrinth, GitHub config, and Maven imports are documented in the
[integration guide](docs/integrations.md).

SolderPy Loader, MCInstance Loader, Packwiz, FileDirector, Modpack Director,
Modrinth, CurseForge, and Prism setup,
export usage, and file layouts are documented in the
[distribution usage and format guide](docs/distribution-formats.md).

+ **Easy install with docker**

+ **Efficient user experience**

  solder.py is designed to allow a minimal button clicking as possible.

  + **Selected build feature**

    This feature allows the user to select a modpack build that the user is working on. menu has a own pin for it, new uploaded modversion can be added/updated to selected build directly and more.

  + **Pin your modpacks to menu**

  + **Clone builds from other modpacks**

+ **Mod uploading**

  + **S3/R2 bucket compatibility**

  + **Required mod dependencies**

    Configure one or more dependencies on a mod's version page. Adding that mod
    to a build also adds the newest matching dependency version, including
    transitive dependencies, while preserving versions already in the build.

  + **Modrinth integration**

    Search Modrinth from the management interface and link a project to
    the mod library without downloading every release. Selecting a compatible
    version in a build downloads, verifies and packages it on demand. Required
    Modrinth dependencies are imported recursively, recorded in Solder's
    dependency list, and added to the build. Modrinth does not require an API
    key.

  + **GitHub config-pack integration**

    Link a public GitHub repository to an existing `CONFIG` entry. Synchronize
    a manually versioned branch, tag, or commit, or lazily import immutable
    repository tags from the build editor. Repository metadata is removed,
    pinned GitHub submodules are included, and the result is stored as a normal
    Solder ZIP.

  + **Universal Maven integration**

    Configure a standard Maven repository, group ID, artifact ID and optional
    classifier. solder.py reads `maven-metadata.xml`, maps upstream releases to
    Minecraft versions, and downloads only the release selected for a build.

  + **Reviewed integration manifest import and export**

    Import JSON resolved to Modrinth project IDs or exact Maven coordinates,
    with optional name, description, author, link, side, modloader, and
    Minecraft mapping metadata. Export configured integrations to the same
    portable format for review or transfer to another installation.

+ **API only mode**

  Run a public read API with read-only database permissions while keeping a
  separate management instance on a private network.

+ **Shadow builds**

  A shadow build is generated from an existing build using its side and
  optional metadata.

  + **Optional shadow build**

    Mark a mod optional per build, useful for additions such as shaders or
    higher-demand client features.

  + **Advanced optional groups**

    Give SolderPy Loader, MCIL, FileDirector, and Modpack Director choices
    names, independent or exact-one selection rules, defaults, and a
    Technic/basic state of Required, Optional, or Excluded. Public builds can
    optionally bootstrap SolderPy Loader through a normal Technic Solder
    package so the same choices are available before mod discovery.

  + **Server shadow build**

    Mark mods for the client, server, or both and expose a server-compatible
    view of the same build. Export a small dedicated-server ZIP containing
    SolderPy Loader and Relauncher in `mods/` plus one verified server-side
    `LAUNCHER` JAR, such as Crucible, at the archive root; the remaining
    server content is installed and updated from the bootstrap API.

+ **Internal notation on mods**

+ **Generate changelog**

+ **MCInstanceLoader export support**

  Export a build from its management page as an MCInstanceLoader
  `.mcinstance` archive.

+ **SolderPy Loader, Packwiz, FileDirector, and Modpack Director support**

  Export a SolderPy Loader configuration ZIP, or let Technic, Modrinth,
  CurseForge, and Prism deliver the Loader and Relauncher while using the same
  dedicated API. Packwiz metadata and Director-compatible bundles remain
  available as separate formats.
  Modpack Director exports include its `modpack.json` pack identity and update
  metadata. Each format is independently enabled in the settings GUI, and the
  hosted routes are available in API-only mode.

+ **Modrinth and CurseForge pack exports**

  Export a build as an MRPack or CurseForge archive. Modrinth-mapped versions
  remain native in MRPack; other packages are installed from Solder through
  SolderPy Loader, MCInstance Loader, FileDirector, or Modpack Director where
  available.
  CurseForge packs contain only the selected
  downloader and any enabled Modrinth-CurseForge sync mappings as native projects.
  Solder API only and hybrid modes either use the Solder repository for build
  packages or use exact Modrinth CDN files where a mapping exists and Solder
  for everything else. The export window lets you
  choose a compatible downloader release loaded from the
  target platform API and temporarily override the exported Forge/modloader
  version without changing the saved build.

+ **Prism Launcher instance exports**

  Export a directly importable Prism/MultiMC instance containing
  `mmc-pack.json` and `instance.cfg`. Use a verified SolderPy Loader, MCIL, or
  FileDirector bootstrap for a small archive, or use the self-contained
  fallback when the build cannot use a downloader. Minecraft and the modloader remain
  launcher-managed.

+ **Database compatibility with Technic Solder**

  solder.py preserves the core Technic tables and adds the fields and tables
  needed for its additional features.

## Modrinth imports

Open **Browse mods** in the management menu to search and add provider-managed
mods. No project file is downloaded at this stage. In a modpack build, select
the linked mod and then a compatible provider version. solder.py downloads the
upstream JAR, verifies the provider hash and file size, packages the JAR as a
normal Solder ZIP, and records the local version. Re-selecting it reuses the
stored version. Required Modrinth projects are imported recursively and linked
through the normal Solder dependency list. Exact dependency versions are used
when Modrinth supplies them; otherwise solder.py selects the newest compatible
release.

See the [integration guide](docs/integrations.md) for storage,
permissions and operational details.

An existing local mod with a different slug can be linked manually from its
management page using a Modrinth URL, slug, or project ID. This keeps its local
slug, versions, and build assignments.

## GitHub config imports

Set an existing mod's type to `CONFIG`, save it, and use **Link GitHub** on its
management page. A manual sync accepts a branch, tag, or commit plus an explicit
version. Tagged repositories also expose their tags in the build editor and to
**Update all mods**, so only the selected immutable tag is downloaded and
packaged. This integration reuses the existing provider columns and adds no
GitHub-specific database tables.

Repositories can include a `.solderpyignore` file to omit development,
server-only, or generated paths from the packaged config ZIP. It supports
gitignore-style wildcards, root-relative rules, comments, and `!` re-inclusion.

See the [integration guide](docs/integrations.md) for archive filtering,
submodule handling, limits, and credentials.

## Maven imports

Open **Maven** in the management menu and add the repository base URL first.
Then add a mod using its group ID, artifact ID, optional classifier, and JAR
extension. Maven implementations only need to expose the standard repository
layout and `maven-metadata.xml`; solder.py does not require an Artifactory,
Nexus, or other vendor-specific API.

Each artifact has one explicit Minecraft mapping mode:

- **Contained in Maven version** uses a format such as
  `{minecraft}-{version}`. For example, `1.7.10-9.10.48` maps to Minecraft
  `1.7.10` and mod version `9.10.48`.
- **Always one Minecraft version** assigns every upstream release to the fixed
  Minecraft version selected by the administrator.
- **Manual per version** leaves new releases disabled until their Minecraft
  and mod versions are entered on the artifact page.

Refreshes preserve manual corrections and mark releases removed from upstream
metadata as unavailable. The build editor and **Update all mods** only consider
available, enabled releases matching the build's Minecraft version and
modloader. Listing or refreshing releases downloads metadata only. The selected
JAR is downloaded, checksum-verified when Maven publishes a standard checksum
sidecar, validated as a JAR, and packaged into the normal Solder repository.
Timestamped Maven snapshots are resolved through their version-level metadata.
On an artifact's management page, **Use the Maven JAR URL directly** lets
SolderPy Loader download that exact native Maven artifact before falling back
to the Solder-hosted JAR. This is opt-in and requires a public HTTPS Maven
repository. The downloaded bytes are always checked against the raw-JAR MD5
stored when solder.py imported the version.

See the [integration guide](docs/integrations.md) for the complete workflow.

## Distribution exports

Enable the required formats under **Settings > Env Settings**, then open a
build's **Export** window. The complete
[distribution usage and format guide](docs/distribution-formats.md) explains
the shared Solder API only and hybrid options, SolderPy Loader and MCInstance
Loader archive mapping, Packwiz, FileDirector, and Modpack Director hosting,
Modrinth or CurseForge fallback exports, and Prism Launcher bootstrap or
self-contained instance exports.

Direct publishing is configured under **Settings > Publishing**. Add a
Modrinth personal access token or CurseForge author API token, map a
local modpack to an existing remote project, and use **Publish** in the build's
Export window. solder.py generates the same archive offered by the normal
download button, uploads it as a new remote version, and records the result.
If that live build is changed after a successful publication, the action becomes
**Send patch** and creates `-Patch-1`, `-Patch-2`, and later remote versions.
Publishing accounts and mappings belong to the solder.py user who created them;
another user who manages the same modpack must configure their own upload token
and mapping.

## Server and optional API manifests

solder.py keeps the Technic read API paths and response defaults. A normal
launcher request remains unchanged:

```text
GET /api/modpack/example-pack/1.0
```

Clients that understand solder.py's shadow-build features can request a target
and optional packages with query arguments:

```text
GET /api/modpack/example-pack/1.0?target=server
GET /api/modpack/example-pack/1.0?optional=true
GET /api/modpack/example-pack/1.0?target=server&optional=true
```

`target=client` includes `CLIENT` and `BOTH` packages. `target=server` includes
`SERVER` and `BOTH` packages. Optional packages are excluded unless
`optional=true` is supplied. The existing `-optional` and `-server` build
suffixes remain supported as aliases.

Server updaters can use `recommended` or `latest` in place of a build version.
Extended manifests report the resolved version, target, optional mode and a
stable SHA-256 manifest hash. Their mod entries also report `side`, `modtype`,
build-specific optional status and declared dependencies:

```text
GET /api/modpack/example-pack/recommended?target=server
```

Supplying an installed build in `from` also returns package-level additions,
updates and removals:

```text
GET /api/modpack/example-pack/latest?target=server&from=1.0
```

The comparison describes Solder packages. A server updater remains responsible
for tracking which files were extracted from each package when removing an old
package.

# Installation and updating

The supported installation is Docker Compose with MySQL and a reverse proxy.
The example below also gives the mod repository its own web server: Caddy
serves ZIPs and JARs directly while proxying solder.py to Gunicorn. This avoids
serving large mod files through Python and does not require S3 or R2.

Before starting, you need:

- a Linux host with Docker Engine and the Compose plugin;
- a domain pointing to the host;
- inbound TCP ports 80 and 443;
- persistent storage for MySQL, Caddy, and the mod repository;
- current backups of the database and repository when upgrading or migrating.

The application container must not be exposed directly to the internet. Use
Caddy, NGINX, or another trusted reverse proxy for HTTPS. The Compose example
keeps both MySQL and Gunicorn on private Docker networks.

Useful follow-up documentation:

- [solder.py for Technic Solder users](docs/technic-solder-users.md)
- [mod and modpack management guide](docs/management-guide.md)
- [GitHub config repository guide](docs/github-config.md)
- [distribution usage and format guide](docs/distribution-formats.md)
- [dedicated Solder bootstrap API guide](docs/bootstrap-api.md)
- [environment variable template](.env.example)

Modrinth and CurseForge archives can also include administrator-configured
native bootstrap projects. Manage these under **Settings > Modrinth-CurseForge sync**;
TX Loader is supplied as a disabled default mapping. See the distribution
guide for how sync mappings interact with Solder API only and hybrid exports.

## Recommended Docker Compose installation

If you do not want to use S3 or R2, the recommended Docker setup is to let
[Caddy](https://hub.docker.com/_/caddy) serve the local mod repository and
reverse proxy solder.py. Caddy serves large files below `/mods/` directly and
proxies the dynamic site, API, Packwiz metadata, and Director manifests to
Gunicorn. Only Caddy is exposed to the internet. The solder.py container writes
files to the shared repository while Caddy mounts that repository read-only;
MySQL is reachable only on a private Docker network.

This example uses `solder.example.com`. Before starting it:

1. Point the domain's DNS record at the Docker host.
2. Allow inbound TCP ports 80 and 443. UDP port 443 is optional and enables
   HTTP/3.
3. Create the repository directory and make sure the user running the
   solder.py container can write to it:

```bash
sudo mkdir -p /srv/solder/mods
```

Create a file named `Caddyfile` beside the Compose file:

```caddyfile
solder.example.com {
	encode zstd gzip

	# handle_path removes /mods before looking in /srv/solder-mods. A request
	# for /mods/example/example-1.0.zip therefore maps to
	# /srv/solder-mods/example/example-1.0.zip.
	handle_path /mods/* {
		root * /srv/solder-mods
		file_server
	}

	# These files are generated from the selected build. Keep the full path
	# and proxy them to solder.py instead of looking for them under /mods.
	handle /packwiz/* {
		reverse_proxy solderpy:5000
	}

	handle /filedirector/* {
		reverse_proxy solderpy:5000
	}

	handle /modpackdirector/* {
		reverse_proxy solderpy:5000
	}

	# Management pages and the Technic-compatible API.
	handle {
		reverse_proxy solderpy:5000
	}
}
```

Directory browsing is not enabled, so requests to repository directories do
not produce file listings.

Create a Compose `.env` file beside `compose.yml`. Use different, randomly
generated values in production and do not commit this file:

```dotenv
SOLDER_IMAGE=thorfusion/solderpy:1.10.0
SOLDER_DB_PASSWORD=replace-with-a-long-random-password
MYSQL_ROOT_PASSWORD=replace-with-another-long-random-password
SOLDER_SECRET_KEY=replace-with-a-long-random-application-secret
# Optional; required only when CurseForge exports are enabled.
CURSEFORGE_API_KEY=
```

Then create `compose.yml`:

```yaml
services:
  mysql:
    image: mysql:8.4
    restart: unless-stopped
    environment:
      MYSQL_DATABASE: solderpy
      MYSQL_USER: solderpy
      MYSQL_PASSWORD: ${SOLDER_DB_PASSWORD}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h localhost -u root -p\"$$MYSQL_ROOT_PASSWORD\" --silent"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s
    networks:
      - solder_database

  solderpy:
    image: ${SOLDER_IMAGE}
    restart: unless-stopped
    depends_on:
      mysql:
        condition: service_healthy
    expose:
      - "5000"
    environment:
      APP_URL: https://solder.example.com/
      SECRET_KEY: ${SOLDER_SECRET_KEY}
      DB_HOST: mysql
      DB_PORT: "3306"
      DB_USER: solderpy
      DB_PASSWORD: ${SOLDER_DB_PASSWORD}
      DB_DATABASE: solderpy
      PUBLIC_REPO_LOCATION: https://solder.example.com/mods/
      MD5_REPO_LOCATION: /app/mods/
      # Required only when CurseForge archive exports are enabled.
      CURSEFORGE_API_KEY: ${CURSEFORGE_API_KEY:-}
      # Caddy has a fixed address so only it is trusted as the reverse proxy.
      PROXY_IP: 172.30.50.2
    volumes:
      - /srv/solder/mods:/app/mods
    networks:
      solder_frontend:
        ipv4_address: 172.30.50.3
      solder_database:

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    depends_on:
      - solderpy
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - /srv/solder/mods:/srv/solder-mods:ro
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      solder_frontend:
        ipv4_address: 172.30.50.2

networks:
  solder_frontend:
    ipam:
      config:
        - subnet: 172.30.50.0/24
  solder_database:
    internal: true

volumes:
  mysql_data:
  caddy_data:
  caddy_config:
```

Neither MySQL's port `3306` nor solder.py's port `5000` is published. Caddy is
the only public entry point. If `172.30.50.0/24` overlaps an existing Docker or
LAN network, choose another private subnet and update both fixed addresses and
`PROXY_IP`.

Start or update the installation with:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

Caddy automatically obtains and renews HTTPS certificates when the domain
resolves to the server and ports 80 and 443 are reachable. The `caddy_data`
volume must remain persistent because it stores Caddy's certificates and other
state. On a new installation, open `https://solder.example.com/setup` and
create the first administrator after the containers become healthy.

If startup fails, inspect only the application and database logs first:

```bash
docker compose logs --tail=200 solderpy mysql
```

With this layout, the important solder.py repository settings are:

```ini
PUBLIC_REPO_LOCATION=https://solder.example.com/mods/
MD5_REPO_LOCATION=/app/mods/
APP_URL=https://solder.example.com/
SECRET_KEY=replace-with-a-long-random-application-secret
# Required only for CurseForge archive exports.
CURSEFORGE_API_KEY=
```

`PUBLIC_REPO_LOCATION` is the URL used by launchers. `MD5_REPO_LOCATION` is the
local path used by solder.py for hashing and file inspection, so those internal
operations do not need to download the file again through Caddy. `APP_URL` is
the trusted public base URL used in generated FileDirector remote pointers.
`SECRET_KEY` signs management login cookies and must remain private and stable
across restarts and replicas.
`CURSEFORGE_API_KEY` is a CurseForge third-party API key used only to list and
verify downloader files while generating CurseForge archives.

All distribution formats are disabled by default. After setup, open **Settings
> Env Settings**, enable the formats that this installation should publish or
export, and save. Packwiz and FileDirector files are still served when the
solder.py container has `API_ONLY=True`; the enable switches live in MySQL so
management and API-only containers can share them. MRPack and CurseForge
archives are downloaded from the authenticated build editor.

After uploading a mod version, verify both services through Caddy:

```bash
curl -I https://solder.example.com/api/
curl -I https://solder.example.com/mods/example-mod/example-mod-1.0.zip
curl -I https://solder.example.com/packwiz/example-pack/latest/pack.toml
curl -I https://solder.example.com/filedirector/example-pack/latest/mods.bundle.json
```

Replace the example slugs with a public pack and uploaded mod from the
installation. Back up `/srv/solder/mods` and take regular logical MySQL backups;
the `mysql_data` volume, repository, and `caddy_data` volume all persist across
container replacement. The mod repository is user data and is not stored inside
either Docker image.

## Updating a Docker Compose installation

Back up MySQL and the repository first. Change `SOLDER_IMAGE` to the release you
want, then recreate only the services whose image or configuration changed:

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=200 solderpy
```

The management process applies the current additive solder.py schema at
startup. Do not start a read-only API service against a database until the
management service has completed successfully. Keep the previous image tag in
your deployment notes; a database backup is still required because rolling an
application image back does not roll its schema or stored data back.

## Migrating from Technic Solder

Back up the Technic database and repository, then start one management instance
with `TECHNIC_MIGRATION=True`. Its database user needs permission to create and
alter tables. Wait for `technic database migrated!` in the application log,
verify the API and a package download, then remove the setting and restart.

If the imported database has no solder.py administrator, temporarily set
`NEW_USER=True`, open `/setup`, create the account, and immediately remove the
setting. Start a separate `API_ONLY=True` service only after this migration is
complete. The full checklist is in the
[Technic Solder migration guide](docs/technic-solder-users.md#migrating-an-existing-technic-installation).

## Environment and alternative deployment reference

The Compose example above already supplies the required values. Use this
section when adapting it to an external database, S3/R2, or a split API and
management deployment. The complete template is [.env.example](.env.example).

Running directly with Python has limited support. Use the locked Pipenv
dependencies and a production WSGI server; the official image uses Gunicorn.

### Database variables

```dotenv
DB_HOST=mysql
DB_PORT=3306
DB_USER=solderpy
DB_PASSWORD=replace-with-a-long-random-password
DB_DATABASE=solderpy
```

`DB_HOST` is the MySQL hostname as seen from the application container. In the
Compose example it is the service name `mysql`, not `127.0.0.1`.

### Repository variables

`APP_URL` is solder.py's trusted public base URL. Hosted FileDirector and
Modpack Director exports use it instead of deriving a URL from the incoming
request:

```dotenv
APP_URL=https://solder.example.com/
```

Set a stable random secret for management login sessions. Changing it logs out
all signed-in users:

```dotenv
SECRET_KEY=replace-with-a-long-random-application-secret
SESSION_COOKIE_SECURE=True
```

Management session cookies are HTTPS-only by default. Set
`SESSION_COOKIE_SECURE=False` only when developing locally over plain HTTP;
never disable it on a public deployment.

`PUBLIC_REPO_LOCATION` is the HTTP(S) prefix written into launcher manifests.
It must be reachable by players and should include its trailing slash:

```dotenv
PUBLIC_REPO_LOCATION=https://solder.example.com/mods/
```

This is the internal repository source solder.py uses to calculate MD5 hashes and file sizes when rehashing or adding a mod manually. It is separate from the public launcher URL and accepts either:

- A direct HTTP(S) repository URL. Redirects are not followed, so configure the final URL.
- An absolute local repository path, which avoids an HTTP request and is faster. Inside the Docker image, use the container path (normally `/app/mods/`), not the host-side volume path.

Both forms must point to the repository root containing `<mod-slug>/<mod-slug>-<version>.zip`.

```dotenv
MD5_REPO_LOCATION=https://solder.example.com/mods/
```

or:

```dotenv
MD5_REPO_LOCATION=/app/mods/
```

Public GitHub config repositories work without credentials. Installations that
frequently browse or synchronize repository tags can set an optional token to
raise GitHub's API rate limit:

```dotenv
GITHUB_TOKEN=
```

Private GitHub repositories are not imported because Solder repository files
are intended for public launcher distribution.

### Repository volume

solder.py writes local artifacts under `/app/mods` even when object storage is
configured. Mount that path to persistent storage and let Caddy or another file
server expose it. Without a volume, the repository is lost with the container.

```bash
-v /your/path/here:/app/mods
```

### Caching

```dotenv
CACHE_TTL=300
CACHE_SIZE=100
```

`CACHE_TTL` is measured in seconds. `CACHE_SIZE` is the maximum number of
responses retained by each in-process cache.

### S3/R2-compatible object storage

```dotenv
R2_ENDPOINT=
R2_URL=https://files.example.com/
R2_BUCKET=
R2_REGION=
R2_ACCESS_KEY=
R2_SECRET_KEY=
```

Object storage is enabled when `R2_BUCKET` is set. Keep credentials in the
deployment `.env`, never in `compose.yml` or source control.

### Reverse proxy

solder.py trusts one reverse proxy address for forwarded client IPs. Use the
proxy's address as seen by the application container:

```dotenv
PROXY_IP=172.30.50.2
```

Do not trust an arbitrary public range. The fixed private address in the
Compose example keeps this boundary explicit.

### Setup and migration switches

`NEW_USER=True` temporarily re-enables `/setup` for an existing database.
Disable it immediately after creating the required account.

```dotenv
NEW_USER=True
```

Enable this while starting a management instance against a Technic Solder
database. The current Technic schema is detected and the solder.py columns and
tables are added without removing or rewriting Technic fields. Migration runs
once during application startup; `/setup` no longer performs database changes.

```dotenv
TECHNIC_MIGRATION=True
```

The old form for registering a repository ZIP by version and MD5 is hidden by
default. Enable it only when an installation still uses that manual workflow;
normal uploads and provider imports are unaffected:

```dotenv
ENABLE_LEGACY_MODVERSION_ADDING=True
```

### Split API and management modes

`API_ONLY=True` exposes the read API and public distribution files without the
login or management interface. It is suitable for a public service using
read-only database credentials.

```dotenv
API_ONLY=True
```

`MANAGEMENT_ONLY=True` exposes the management side without the read API.

```dotenv
MANAGEMENT_ONLY=True
```

### Writable API

The authenticated write API is disabled by default. Enable it only on the
trusted management deployment, such as the instance behind your VPN:

```dotenv
WRITE_API=True
```

Create the initial bearer token from **Settings > API Tokens** after logging
in. Tokens use the owner's existing solder.py permissions and modpack access.
`API_ONLY=True` remains read-only unless `WRITE_API=True` is also set; an API
instance with writes enabled requires a database user with write permissions.
See the [write API reference](docs/write-api.md) for routes and examples.

## Initial web setup

For a clean database, wait until MySQL and solder.py are healthy, then open
`https://solder.example.com/setup`. Enter the first administrator's email and a
strong password. After submission, sign in through `/login`.

If `/setup` reports a database error, do not keep retrying account creation.
Check `docker compose logs solderpy mysql`, correct the database connection,
and restart the services. On an existing database, `/setup` is closed when a
user already exists unless `NEW_USER=True` was deliberately enabled.

After the first login:

1. Verify the public URL and repository paths under **Settings > Env Settings**.
2. Enable only the distribution formats that will be used.
3. Upload a small test mod and verify its ZIP through the public `/mods/` URL.
4. Create an unpublished private test build and verify it in management.
5. Publish a test build, verify its exact API route, and then set the desired
   `latest` and `recommended` channels.
6. Configure scheduled database and repository backups.

## Development environment

```bash
python -m pip install pipenv
python -m pipenv install
python -m pipenv run app

python -m pipenv lock
```

## Tests

The test suite uses Python's standard library and does not need a live MySQL
database or object-storage account. Install the locked application dependencies,
then run all tests:

```bash
python -m pip install pipenv
python -m pipenv sync
python -m pipenv run python -m unittest discover -s tests -v
```

The production container and both MySQL fixtures can be tested locally with:

```bash
docker build --tag solderpy:database-test .
python -m pipenv run python tests/database_container_smoke.py solderpy:database-test
```

The checked-in SQL fixtures contain only table definitions and synthetic `ci-*`
records. The original populated dumps remain ignored and must not be committed.

GitHub Actions also verifies the lock file, imports/compiles the Python sources,
checks the installed dependency set, and runs the same suite on every pull
request and trusted branch push.

The weekly dependency updater needs a repository secret named
`DEPENDENCY_UPDATE_TOKEN`. Use a fine-grained token from an automation account
with access only to this repository and only **Contents: read/write** and
**Pull requests: read/write**. This separate token ensures the updater's pull
request triggers the required checks; the workflow never approves or merges it.

To prevent an untested pull request from being merged, add a branch rule for
`main`, enable **Require status checks to pass before merging**, and select the
`solder.py tests` check. The workflow also supports GitHub's merge queue.

## Security checks

Pull requests and trusted branch pushes run several complementary checks:

- CodeQL scans the Python and JavaScript sources with extended security queries.
- Bandit checks Python-specific security problems.
- pip-audit checks every locked production dependency for known vulnerabilities.
- Trivy checks the built production image for fixable high and critical operating
  system and Python-package vulnerabilities, and publishes SARIF results to the
  repository Security tab. The same workflow boots the image and verifies the
  `/api/` health endpoint before the scan.
- Container integration tests restore sanitized, synthetic versions of both a
  Technic Solder v1.3.1 database and a solder.py 1.7.4 database. They verify the
  Technic migration twice for idempotency, create a fresh database, and prove
  API-only startup works with read-only database credentials. They then exercise
  API keys, clients, modpacks, builds, and mod versions against a real MySQL
  server. The API checks cover public, hidden, private, unpublished, optional,
  server, and hyphenated build cases.
- GitHub dependency review checks dependency changes made by pull requests.

Run the Python checks locally with:

```bash
python -m pipenv sync --dev
python -m pipenv run python audit_dependencies.py
python -m pipenv run bandit --recursive app.py api.py alogin.py asetup.py asite.py models --severity-level medium --confidence-level medium
```

The `Python security` and `Container security` checks can also be selected as
required status checks in the `main` branch rule.
