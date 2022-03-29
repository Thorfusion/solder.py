# Solder structure

Because Solder doesn't do any file handling yet you will need to manually manage your set of mods in your repository. The mod repository structure is very strict and must match your Solder data exact. An example of your mod directory structure will be listed below:

+ /mods/[modslug]/
+ /mods/[modslug]/[modslug]-[version].zip

## Structure for the mods folder at the solder server
### Example 1

+ mods/
  + [modslug]/
    + [modslug]-[version].zip

## Structure for zipped files
### Example 1 (Regular mods):

+ [modslug]-[version].zip
  + mods/ 
    + modA-1.0.jar

### Example 2 (Configs):

+ [configpack]-[confver].zip 
  + config/ 
    + configA.cfg 
    + configB.cfg 
    + config.txt 
  + server.dat

### Example 3 (Forge):

+ [forge]-[forgever].zip 
  + bin/ 
    + modpack.jar (Forge universal binary jar)
    + version.json (other modlaunchers and newer forge versions)


This work is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License. 
CC BY NC SA 4.0
