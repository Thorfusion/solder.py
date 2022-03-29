
he du mulighet å fiksa de me mod opplasting the solder
alt den trenge å gjøre er at eg velger vilke mod eg ska lasta opp til, så henter den hva modid i solder er fra databasen, eg kan legge inn versjonnummeret selv og den hasher md5 og legger det inn i databasen

alt blir lagra i en mappe som hete mods i samme plass som programmet e
ka tenke du? kan ver eget programm, ikkje nødvendigvis i solder
Noe du he lyst å gjør? Kan gi litt peng om det hjelpe


Kan hjelpe me det eg kan
Tenke du at me skala laga et eget programm, nettside elle bygge det inn i solder
Og en knapp hvor vi kan bytte mellom mods/mod.jar modus til zip hvor vi laster opp zip og den blir renama istede for å legge en jar i mods i en zip. For noen ting er utenfor jar filer
En manuell modus

mysql database
det er to tabeller i solder den må hente tilgang til, en er for å hente mod liste, modid og modname

modid i flowcharten er modname i databasen
det vi trenger å hente fra her er mod_id og mod_name
hvor vi setter mod_ id inn i mod_id i modversions tabellen og mod_name vil bli brukt til modid i flowcharten

den første bilder er tabellen modversions og en siste er mods tabellen
bare modsversion vi trenger å putte noe i, mens vi henter info fra mods tabellen