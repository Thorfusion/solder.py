![solder.py](https://files.thorfusion.com/images/solderwhite.py.png)

# What is solder?

>Technic Solder is an API that sits between a modpack repository and the launcher. It allows you to easily manage multiple modpacks in one single location. It's the same API we use to distribute our modpacks!
>
>Using Solder also means your packs will download each mod individually. This means the launcher can check MD5's against each version of a mod and if it hasn't changed, use the cached version of the mod instead. What does this mean? Small incremental updates to your modpack doesn't mean redownloading the whole thing every time!
>
>-- Technic

# About solder.py

With critical missing features in technic solder and solder.cf now costs money and is mothballed right now we needed an more modern solder to use.
With new features like allowing solder to be used on multiple launchers and much simpler in design than original solder, its much easier to install and maintain.
We strive to keep the efficiency and simplicity of use at a top, removing unnecessary steps and keeping a simple look.
solder.py is even compatible with original solder's database, visit the install section below.

## Features over original solder

+ Easy install with docker
+ Native Mod uploading
+ Internal descriptions of mods
+ Function that allows mod version being uploaded/added to auto add/replace in selected build
+ Clone builds from other modpacks
+ R2 bucket integration with mod uploading, host your files on the cloud!  
+ MCIL optional mod tag
+ Generate changelog
+ Pin your favorite modpacks to your menu!

# Features to be added in the future

+ Maven integration
+ Modrinth integration
+ MCIL export support
+ Advanced user management

## Unfinished Features in dev

# Installation/Updating

solder.py is compatible with the original database and only adds columns to tables for the extra features we have, you can even dual run with both solder.py and original solder.
Users of solder.cf need to use the migrating tool which isn't available at this stage.

## Pre-install

You need to be familiar with hosting websites and linux. We will help anyone who needs help but google is a good friend.

### Requirements (work in progress)

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

Well we dont have your database name nor username so go get it!

## Install solder.py

You need to set the enviroment variables either through host or as a .env file, see the .env.example for further use

solder.py needs to be run by an production wsgi, our docker image uses gunicorn

```bash

```

## Install solder.py with docker

### Pull the latest image from docker hub

```bash
docker pull thorfusion/solderpy
```

### Launch an container, remember to also add the enviroment variables further down

```bash
docker run --name solderpy --restart always -d -p 80:5000 thorfusion/solderpy
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

```bash
-e SOLDER_MIRROR_URL=https://solder.example.com/mods/
```

This is the url solder.py uses to calculate md5 and filesize when rehashing or adding a mod manually. This is currently only url, so use localhost or local ip address if on the same network, else use same url as mirror url.

```bash
-e SOLDER_REPO_LOCATION=https://solder.example.com/mods/
```

#### Volumes

Solder.py uploads the modfiles to a volume in the container

```bash
-v /your/path/here:/app/mods
```

#### R2 Bucket Variables

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

Setup screen will take some time as when requesting this page, solder.py is creating the other tables in the database. when done the user will be presented with a email and password box with a setup button in the bottom, this is the administrator account you are creating. when setup is done, you will be redirected to login screen

#### Step 3 Login screen again

You will be redicted to log in screen, you can now login and be redirected to the index page, solder.py install is now complete. remember to disable NEW_USER and TECHNIC_MIGRATION as this allows the setup page to be up and random users to create accounts.

## Dev Enviroment

```bash
python -m pip install pipenv
python -m pipenv install
python -m pipenv run app

python -m pipenv lock
```
