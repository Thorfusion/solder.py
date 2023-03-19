![solder.py](https://files.thorfusion.com/images/solderwhite.py.png)

# What is solder?
>Technic Solder is an API that sits between a modpack repository and the launcher. It allows you to easily manage multiple modpacks in one single location. It's the same API we use to distribute our modpacks!
>
>Using Solder also means your packs will download each mod individually. This means the launcher can check MD5's against each version of a mod and if it hasn't changed, use the cached version of the mod instead. What does this mean? Small incremental updates to your modpack doesn't mean redownloading the whole thing every time!
>
>-- Technic

# About solder.py
With critical missing features in technic solder and solder.cf mothballed right now we needed an more modern solder to use. 
With new features like allowing solder to be used on multiple platforms and much simpler in design than original solder, its much easier to install and maintain.
We strive to keep the efficiency and simplicity of use at a top, removing unnecessary steps and keeping a simple look.
solder.py is even compatible with original solder's database, visit the install section below.

## Features over original solder (features not done yet)
+ Designed by and for modpack creators, allowing fewer clicks therefore less time consuming
+ Mod upload
+ MCIL export support with optional mod tag
  + finally solder is multi launcher compatible
+ Easy install with docker
+ Internal descriptions of mods
+ Pin your favorite modpacks to your menu!
+ Clone builds from other modpacks
+ [Planned]R2 bucket integration, host your files on the cloud!
+ [Planned]Maven integration
+ [Planned]Modrinth integration

Note that planned features will not be available on first release

## Working features
+ Technic solder api
  + yes, solder.py can actually host, you can't edit stuff yet
+ Basic view of solder

## Features in dev
+ Mod uploading
+ MCIL export support
+ Internal descriptions of mods
+ Editing the solder database

## Native features not to be expected soon
+ Advanced user management

# Installation/Updating

solder.py is compatible with the original database and only adds columns to tables for the extra features we have, you can even dual run with both solder.py and original solder.
Users of solder.cf need to use the migrating tool available

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
```mysql
CREATE USER 'solderpy'@'localhost' IDENTIFIED BY 'passwordsecret';
```
Create a database and give the user access to it
```mysql
CREATE DATABASE solderpy;
GRANT ALL ON solder.* TO 'solderpy'@'localhost';
FLUSH PRIVILEGES;
exit
```

## Setting up MySQL: Migrating to solder.py
Well we dont have your database name nor username so go get it!

## Install solder.py
```bash

```


## Install solder.py with docker
Pull the latest image from docker hub
```bash
docker pull thorfusion/solderpy
```

Launch an container, underneath is an example, remember to also add the enviroment variables further down.
```bash
docker run --name solderpy --restart always -d -p 80:5000 thorfusion/solderpy
```

Enviroment variables
```bash
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=user
DB_PASSWORD=password
DB_DATABASE=solderpy
```

The docker image does not and will not support https, therefore it is required to run an reverse proxy

## TODO for docker
Enable filehosting for like mods n shit

## Dev Enviroment

```bash
python -m pip install pipenv
python -m pipenv install
python -m pipenv run app
```
