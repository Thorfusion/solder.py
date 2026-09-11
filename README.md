![solder.py](https://files.thorfusion.com/images/solderwhite.py.png)

# What is solder?

>Technic Solder is an API that sits between a modpack repository and the launcher. It allows you to easily manage multiple modpacks in one single location. It's the same API we use to distribute our modpacks!
>
>Using Solder also means your packs will download each mod individually. This means the launcher can check MD5's against each version of a mod and if it hasn't changed, use the cached version of the mod instead. What does this mean? Small incremental updates to your modpack doesn't mean redownloading the whole thing every time!
>
>-- Technic

# About solder.py

solder.py is solder written in python with major features over technic's solder.

The complete read API, including Technic-compatible routes and solder.py server
and optional-manifest extensions, is documented in the
[API reference](docs/api.md).

Management-side Modrinth and CurseForge imports are documented in the
[provider integrations guide](docs/integrations.md).

+ **Easy install with docker**

+ **Efficient user experience**

  solder.py is designed to allow a minimal button clicking as possible.

  + **Selected build feature**

    This feature allows the user to select a modpack build that the user is working on. menu has a own pin for it, new uploaded modversion can be added/updated to selected build directly and more.

  + **Pin your modpacks to menu**

  + **Clone builds from other modpacks**

+ **Mod uploading**

  + **S3 bucket compatbility**

  + **Required mod dependencies**

    Configure one or more dependencies on a mod's version page. Adding that mod
    to a build also adds the newest matching dependency version, including
    transitive dependencies, while preserving versions already in the build.

  + **Modrinth and CurseForge integration**

    Search either provider from the management interface and link a project to
    the mod library without downloading every release. Selecting a compatible
    provider version in a build downloads, verifies and packages it on demand.
    Modrinth needs no key; every management user supplies their own CurseForge
    API key.

+ **API only mode**

  Host a public api with only read permission to database and have another instance with manegement in your local network

+ **Shadow builds**

  An shadow build is a build that is generated automatically out of your created build, based on set factors.

  + **Optional shadow build**

    solder.py allows user to set a mod as an optional in a modpack build. enabling optional builds on a modpack, useful for modpack devs that want to provide a more demanding build with shaders.

  + **Server shadow build**

    solder.py allows user to set client/server side on mods, this shadow build only contain server compatible mods

+ **Internal notation on mods**

+ **Generate changelog**

+ **MCInstanceLoader export support**

  Export a build from its management page as an MCInstanceLoader
  `.mcinstance` archive.

+ **Database compatbility with technic solder**

  solder.py only adds extra tables and columns and can be dual run with technic solder

# Features to be added in the future

+ Maven integration

## Modrinth and CurseForge imports

Open **Browse mods** in the management menu to search and add provider-managed
mods. No project file is downloaded at this stage. In a modpack build, select
the linked mod and then a compatible provider version. solder.py downloads the
upstream JAR, verifies the provider hash and file size, packages the JAR as a
normal Solder ZIP, and records the local version. Re-selecting it reuses the
stored version.

CurseForge requires an API key. Each management user enters their own key on
the Browse mods page; there is no shared key bundled with solder.py. Only
available CurseForge projects whose authors allow third-party distribution can
be imported. See the [provider integrations guide](docs/integrations.md) for
storage, permissions and operational details.

## MCInstanceLoader exports

The **Export MCIL** action on a modpack's build list creates the
[MCInstanceLoader 2.7 archive format](https://github.com/HRudyPlayZ/MCInstanceLoader/tree/1.7.10).
`PUBLIC_REPO_LOCATION` must be configured because the generated resource list
uses the public repository URLs. Build create/edit forms store the modloader
and its optional version (in Technic's existing `forge` version column) for
`metadata.packconfig`.

The export maps Solder packages as follows:

- A `MOD` uploaded from a JAR becomes a downloadable MCIL resource in `mods/`,
  including its side, optional status and verified raw-JAR MD5.
- `CONFIG`, `RES`, `NONE`, and older mandatory `MOD` packages without a raw JAR
  are unpacked into `overrides`, `client-overrides`, or `server-overrides`
  according to their configured side.
- `MCIL` identifies the MCInstanceLoader package itself and is excluded from
  the payload. `LAUNCHER` packages are also excluded because their Technic
  `bin/` contents are launcher-specific.
- Optional bundled ZIPs are rejected because MCInstanceLoader can only toggle
  individual download resources. Optional server-only packages are rejected
  because MCInstanceLoader 2.7 presents optional choices only on clients.

The browser still calculates upload MD5 values so the interface remains
responsive. Before adding the version to the database, solder.py independently
hashes the received Solder ZIP and its extracted raw JAR and rejects a mismatch.
It also gives the raw JAR its canonical `<slug>-<version>.jar` repository name.

For versions imported from Technic Solder or created before raw JAR storage was
available, use **Create MCIL JAR** on the mod's version page. Solder.py verifies
the existing ZIP against its stored MD5, extracts the single JAR under `mods/`,
stores the canonical raw JAR locally and in S3/R2 when configured, and records
the verified JAR MD5. It reads the ZIP from the local repository first and falls
back to `MD5_REPO_LOCATION`, so remotely hosted legacy repositories can be
upgraded without re-uploading each mod.

Mod versions can be assigned a modloader in both upload flows. Leaving it blank
makes the version loader-agnostic, just as a null mod Minecraft version is
universal. Build management lists and accepts only versions matching both the
build's Minecraft version and modloader; required dependencies use the same
matching rules. Existing Technic builds with a Forge version are identified as
`FORGE` automatically.

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

# Installation/Updating

solder.py is compatible with the original database and only adds columns to tables for the extra features we have, you can even dual run with both solder.py and original solder.
Users of solder.cf need to use the migrating tool which isn't available at this stage.

+ [Setting up MySQL](#setting-up-mysql-for-new-installations)
+ [Setting up Reverse Proxy](#setting-up-reverse-proxy)
+ [Setting up Filehosting](#setting-up-filehosting--s3r2-bucket)
+ [Installing solder.py](#install-solderpy-with-docker)
  + [solder.py enviroment variables](#enviroment-variables-for-docker-image)
  + [solder.py docker example](#example)
  + [Web Setup solder.py](#docker-container-installed-setup-on-website)

## Pre-install

You need to be familiar with hosting websites and linux. We will help anyone who needs help but google is a good friend.

### Recommended requirements

+ MySQL Server
+ Docker
+ Apache, NGINX or equivalent for reverse proxy
+ Filehosting server or S3/R2 bucket.

NOTE: Hosting solder.py manually and not with the official docker image, support is limited.

## Setting up MySQL: for new installations

go to mysql

```bash
mysql
```

Create a new user

```sql
CREATE USER 'solderpy'@'localhost' IDENTIFIED BY 'passwordsecret';
```

Create a database and give the user access to it

```sql
CREATE DATABASE solderpy;
GRANT ALL ON solderpy.* TO 'solderpy'@'localhost';
FLUSH PRIVILEGES;
exit
```

### Setting up MySQL: Migrating to solder.py

Get your database name and user information

## Setting up Reverse proxy

For NGINX users when your reverse proxy is the only proxy in the chain, you use this in your location

```conf
proxy_set_header X-Forwarded-For $remote_addr;
```

However if your reverse proxy is behind another one, like cloudflare you need to use this forwarding instead

```conf
proxy_set_header X-Forwarded-For $http_x_forwarded_for;
```

## Setting up Filehosting / S3/R2 bucket

You can use the same nginx/apache server for both reverse proxy and filehosting.

## Install solder.py with python (Limited support)

You need to set the enviroment variables either through host or as a .env file, see the .env.example for further use

solder.py needs to be run by an production wsgi, our docker image uses gunicorn

## Install solder.py with docker

### Pull the latest image from docker hub

```bash
docker pull thorfusion/solderpy:latest
```

### Launch an container, remember to also add the enviroment variables further down

```bash
docker run --name solderpy --restart always -d -p 80:5000 thorfusion/solderpy:latest
```

### Enviroment variables for docker image

#### Database variables

```bash
-e DB_HOST=127.0.0.1
```

```bash
-e DB_PORT=3306
```

```bash
-e DB_USER=user
```

```bash
-e DB_PASSWORD=password
```

```bash
-e DB_DATABASE=solderpy
```

#### Repo variables

This is the public facing URL for your repository. This is prefix url that technic launcher uses to download the mods. Include a trailing slash!
You dont actually need to store the files in mods but solder.py own hosting folder is mods. S3 public links can be root for an example, same with repo url aswell.

```bash
-e PUBLIC_REPO_LOCATION=https://solder.example.com/mods/
```

This is the url solder.py uses to calculate md5 and filesize when rehashing or adding a mod manually. This is currently only url, so use localhost or local ip address if on the same network, else use same url as public repo url.

```bash
-e MD5_REPO_LOCATION=https://solder.example.com/mods/
```

#### Volumes

Solder.py uploads the modfiles to a volume in the container regardless of S3 functionality. By setting the volume This folder can be accessed by apache/nginx so you can host its files. solder.py does not host the mod files as python isn't really suited for this.

If not specified, mods will not be persistant.

```bash
-v /your/path/here:/app/mods
```

#### Caching options

Set the cache time to live, default 300 (seconds)

```bash
-e CACHE_TTL=300
```

Set the cache maximum size ie how much memory usage. Default is 100 (MB).

```bash
-e CACHE_SIZE=100
```

#### S3/R2 Bucket Variables

```bash
-e R2_ENDPOINT=
```

```bash
-e R2_ACCESS_KEY=123
```

```bash
-e R2_SECRET_KEY=123
```

Note that R2 Bucket functionality gets activated when R2_BUCKET is used

```bash
-e R2_BUCKET=
```

```bash
-e R2_REGION=
```

#### Reverse Proxy Variables

solder.py only trusts one reverse proxy at a time. solder.py will work fine without these variables, but only one user can stay logged in at a time.

```bash
-e PROXY_IP=192.168.1.1
```

#### Adding a new user

Enables the /setup page if the database already exists and you need to add a new user

```bash
-e NEW_USER=True
```

#### Upgrading technic solder database to solder.py, keeps compability to technic solder

Enable this while starting a management instance against a Technic Solder
database. The current Technic schema is detected and the solder.py columns and
tables are added without removing or rewriting Technic fields. Migration runs
once during application startup; `/setup` no longer performs database changes.

```bash
-e TECHNIC_MIGRATION=True
```

#### API only mode

solder.py will only run the api part of solder, allows it to run on read only permissions on a database, no login. good fit for public facing api only mode and another solder.py instance for local access only or similar for login and management

```bash
-e API_ONLY=True
```

#### Management only mode

solder.py will run everything except api part of solder, quite rare usecase.

```bash
-e MANAGEMENT_ONLY=True
```

#### Example

```bash
docker run -d \
  --name solderpy \
  -e DB_HOST=192.168.1.1 \
  -e DB_PORT=3306 \
  -e DB_USER=solderpyuser \
  -e DB_PASSWORD=solderpypassword \
  -e DB_DATABASE=solderpydb \
  -e MD5_REPO_LOCATION=http://example.com/mods/ \
  -e SOLDER_REPO_LOCATION=https://localhost/mods/ \
  -e PROXY_IP=192.168.1.2\
  -p 80:5000 \
  -v /solderpy/mods:/app/mods \
  --restart unless-stopped \
  thorfusion/solderpy:latest
```

NOTE: The docker image does not and will not support https, therefore it is required to run an reverse proxy

### docker container installed, setup on website

Set `NEW_USER=True` and `TECHNIC_MIGRATION=True` for an existing Technic
database. Wait for startup migration to complete before starting a separate
read-only API instance. For a clean installation, leave both disabled.

#### Step 1 Login screen

When you have installed the container with the required envirables and created an mysql database and user that has access to said database, next step is to go to solder.py (<http://example.com>) where you will be redirected to login screen. by seeing this login screen, an successful connection to the database has been achieved. where you can find a table named sessions. go to (<http://example.com/setup>)

#### Step 2 Setup screen

user will be presented with a email and password box with a setup button in the bottom, this is the administrator account you are creating. when setup is done, you will be redirected to login screen

#### Step 3 Login screen again

You will be redicted to log in screen, you can now login and be redirected to the index page, solder.py install is now complete. remember to disable NEW_USER and TECHNIC_MIGRATION as this allows the setup page to be up and random users to create accounts.

## Dev Enviroment

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
