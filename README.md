![solder.py](https://files.thorfusion.com/images/solderwhite.py.png)

# What is solder?
>Technic Solder is an API that sits between a modpack repository and the launcher. It allows you to easily manage multiple modpacks in one single location. It's the same API we use to distribute our modpacks!
>
>Using Solder also means your packs will download each mod individually. This means the launcher can check MD5's against each version of a mod and if it hasn't changed, use the cached version of the mod instead. What does this mean? Small incremental updates to your modpack doesn't mean redownloading the whole thing every time!
>
>Solder also interfaces with the Technic Platform using an API key you can generate through your account there. When Solder has this key it can directly interact with your Platform account. When creating new modpacks you will be able to import any packs you have registered in your Solder install. It will also create detailed mod lists on your Platform page! (assuming you have the respective data filled out in Solder). Neat huh?
>
>-- Technic

# About solder.py
solder.py is written in flask where ease of use is at top aswell with something stuff here. solder.py is also compatible with technic solder databases making an migration to solder.py easy and simple. With critical missing features in technic solder and solder.cf mothballed right now we needed an more modern solder to use. solder.py is being used by its creators and improvements for efficiency for modpack creators are at top. removing unnecessary steps and keeping it simple.

# Main features over vanilla solder (work in progress)
+ Mod uploading
+ Transfer modpack version between modpacks
+ Internal descriptions of mods

# Requirements (work in progress)
+ MySQL Server
+ Python on Host machine unless running docker
+ Apache or NGINX or equivalent for reverse proxy if applicable

# Installation/Updating

## Pre-install
You need to be familiar with hosting websites and linux. We will help anyone who needs help but google is a good friend.

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

## TODO for docker
Enable filehosting for like mods n shit

## Hosting

## Dev Enviroment

```bash
python -m pip install pipenv
python -m pipenv install
python -m pipenv run app
```
