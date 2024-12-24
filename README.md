![solder.py](https://files.thorfusion.com/images/solderwhite.py.png)

# What is solder?

>Technic Solder is an API that sits between a modpack repository and the launcher. It allows you to easily manage multiple modpacks in one single location. It's the same API we use to distribute our modpacks!
>
>Using Solder also means your packs will download each mod individually. This means the launcher can check MD5's against each version of a mod and if it hasn't changed, use the cached version of the mod instead. What does this mean? Small incremental updates to your modpack doesn't mean redownloading the whole thing every time!
>
>-- Technic

# About solder.py

solder.py is solder written in python with major features over technic's solder.

+ ### Easy install with docker

+ ### Efficient user experience

  solder.py is designed to allow a minimal button clicking as possible.

  + #### Selected build feature

    This feature allows the user to select a modpack build that the user is working on. menu has a own pin for it, new uploaded modversion can be added/updated to selected build directly and more.

  + #### Pin your modpacks to menu

  + #### Clone builds from other modpacks

+ ### Mod uploading

  + #### S3 bucket compatbility

+ ### API only modee

  Host a public api with only read permission to database and have another instance with manegement in your local network

+ ### Shadow builds

  An shadow build is a build that is generated automatically out of your created build, based on set factors.

  + #### Optional shadow build

    solder.py allows user to set a mod as an optional in a modpack build. enabling optional builds on a modpack, useful for modpack devs that want to provide a more demanding build with shaders.

  + #### Server shadow build

    solder.py allows user to set client/server side on mods, this shadow build only contain server compatible mods

+ ### Internal notation on mods

+ ### Generate changelog

+ ### MCInstance Loader Support (in dev)

  Multi launcher support

+ ### Database compatbility with technic solder

  solder.py only adds extra tables and columns and can be dual run with technic solder


# Features to be added in the future

+ Maven integration
+ Modrinth integration

## Unfinished Features in dev

+ MCInstance Loader support

# Installation/Updating

solder.py is compatible with the original database and only adds columns to tables for the extra features we have, you can even dual run with both solder.py and original solder.
Users of solder.cf need to use the migrating tool which isn't available at this stage.

## Pre-install

You need to be familiar with hosting websites and linux. We will help anyone who needs help but google is a good friend.

### Requirements

+ MySQL Server
+ Python on Host machine unless running docker
+ Apache or NGINX or equivalent for reverse proxy if applicable

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
GRANT ALL ON solder.* TO 'solderpy'@'localhost';
FLUSH PRIVILEGES;
exit
```

## Setting up MySQL: Migrating to solder.py

Get your database name and user information

## Install solder.py

You need to set the enviroment variables either through host or as a .env file, see the .env.example for further use

solder.py needs to be run by an production wsgi, our docker image uses gunicorn

```bash

```

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

Solder.py uploads the modfiles to a volume in the container

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

For NGINX users when your reverse proxy is the only proxy in the chain, you use this in your location

```conf
proxy_set_header X-Forwarded-For $remote_addr;
```

However if your reverse proxy is behind another one, like cloudflare you need to use this forwarding instead

```conf
proxy_set_header X-Forwarded-For $http_x_forwarded_for;
```


#### Adding a new user

Enables the /setup page if the database already exists and you need to add a new user

```bash
-e NEW_USER=True
```

#### Upgrading technic solder database to solder.py, keeps compability to technic solder

If new user is enabled, you can enable this migration tool for technic solder database, to migrate it to solder.py, mainly fixes mysql database bugs and adds columns and is reverse compatible with original technic solder

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
  -e DB_USER=solderpy \
  -e DB_PASSWORD=solderpy \
  -e DB_DATABASE=solderpy \
  -e SOLDER_MIRROR_URL=https://example.com/mods/ \
  -e SOLDER_REPO_LOCATION=http://localhost/mods/ \
  -p 80:5000 \
  -v /solderpy/mods:/app/mods \
  --restart unless-stopped \
  thorfusion/solderpy:latest
```

NOTE: The docker image does not and will not support https, therefore it is required to run an reverse proxy

### docker container installed, setup on website

Remember to set NEW_USER and TECHNIC_MIGRATION if using existing technic database, if clean install leave both.

#### Step 1 Login screen

When you have installed the container with the required envirables and created an mysql database and user that has access to said database, next step is to go to solder.py (http://example.com) where you will be redirected to login screen. by seeing this login screen, an successful connection to the database has been achieved. where you can find a table named sessions. go to (http://example.com/setup)

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
