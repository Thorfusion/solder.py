
// Function for search tables
// Thanks https://gist.github.com/effeect/58d50fc7b8db60cf558da183a55eb1ae
function tablesearches(column) {
    var input, filter, table, tr, td, i;
    input = document.getElementById("search");
    filter = input.value.toUpperCase();
    table = document.getElementById("table");
    tr = table.getElementsByTagName("tr");
    for (let i = 0; i < tr.length; i++) {
        td = tr[i].getElementsByTagName("td")[column];
        if (td) {
            txtValue = td.textContent || td.innerText;
            if (txtValue.toUpperCase().indexOf(filter) > -1) {
                tr[i].style.display = "";
            } else {
                tr[i].style.display = "none";
            }
        }
    }
}


// sleep function
// https://stackoverflow.com/questions/16873323/javascript-sleep-wait-before-continuing
function sleep(milliseconds) {
    var start = new Date().getTime();
    for (var i = 0; i < 1e7; i++) {
        if ((new Date().getTime() - start) > milliseconds) {
            break;
        }
    }
}


// Used to set the value inputed to a selected id box, ie text input form gets data from a selected row
function buttonpress(id, val) {
    document.getElementById(id).value = val;
}

function copyformtext(out, input) {
    document.getElementById(out).value = document.getElementById(input).value;
}


// Allows to have delete buttons in a table row, see usages
function submitbuttonpress(id, val, submitid) {
    document.getElementById(id).value = val;
    submit2 = '[name="' + submitid + '"]';
    sleep(25)
    document.querySelector(submit2).click();
}

function submitbuttonpress2(version, name, urlform, submitid) {
    versionname = document.getElementById(version).value;
    urllink = name + '/' + name + '-' + versionname + '.zip';
    document.getElementById(urlform).value = urllink;

    submit2 = '[name="' + submitid + '"]';
    document.querySelector(submit2).click();
}


function submitoptionpress(id, val, id2, val2, submitid) {
    document.getElementById(id).value = val;
    document.getElementById(id2).value = document.getElementById(val2).value;
    submit2 = '[name="' + submitid + '"]';
    document.querySelector(submit2).click();
}

// Used for checkboxes in table rows to check and uncheck them and sending formdata for the update
function submitecheckedpress(id, val, textform, check, submitid) {
    document.getElementById(id).value = val;
    if (document.getElementById(check).checked) {
        document.getElementById(textform).value = "1";
    } else {
        document.getElementById(textform).value = "0";
    }
    submit1 = '[name="' + submitid + '"]';
    sleep(25)
    document.querySelector(submit1).click();
}


/* test code for md5
function buttonpress2() {
    vers = document.getElementById('version').value
    hash = md5(vers);
    document.getElementById('md5').value = hash;
}
*/


// Hashing md5 files
function hashmd5() {
    let fileSelect = document.getElementById('file')
    let files = fileSelect.files
    let file = files[0]

    document.getElementById('filesize').value = file.size;
    var reader = new FileReader();
    reader.onload = function (event) {
        document.getElementById('md5').value = md5(event.target.result)
    };
    reader.readAsArrayBuffer(file);
}

function hashjarmd5(id) {
    let fileSelect = document.getElementById(id)
    let files = fileSelect.files
    let file = files[0]

    var reader = new FileReader();
    reader.onload = function (event) {
        document.getElementById('jarmd5').value = md5(event.target.result)
    };
    reader.readAsArrayBuffer(file);
}


// Calculates the filesize
function filesizecalc(input) {
    document.getElementById('filesize').value = input.files[0].size;
}


// takes an input string and converts it into a slug
function toslug(slug, string) {
    document.getElementById(slug).value = string_to_slug(document.getElementById(string).value);
}


// Function block used in toslug to convert string to slug
// Thanks https://gist.github.com/codeguy/6684588?permalink_comment_id=3777802
function string_to_slug(str) {
    str = str.replace(/^\s+|\s+$/g, ''); // trim
    str = str.toLowerCase();

    // remove accents, swap ñ for n, etc
    var from = "àáãäâèéëêìíïîòóöôùúüûñç·/_,:;";
    var to = "aaaaaeeeeiiiioooouuuunc------";

    for (var i = 0, l = from.length; i < l; i++) {
        str = str.replace(new RegExp(from.charAt(i), 'g'), to.charAt(i));
    }

    str = str.replace(/[^a-z0-9 -]/g, '') // remove invalid chars
        .replace(/\s+/g, '-') // collapse whitespace and replace by -
        .replace(/-+/g, '-'); // collapse dashes

    return str;
}

function hideoptions(optiontoshow) {
    // gets the value of selected option
    selected = document.getElementById(optiontoshow).value;
    // gets all that has the name modlist and makes then hidden
    let modlist = document.querySelectorAll('[name="' + "modlist" + '"]');
    modlist.forEach(el => {
        el.setAttribute('hidden', 'true')
    })
    // uses the selected options value to only show matching id's
    let modversion = document.querySelectorAll('[id="' + "modversion_" + selected + '"]');
    modversion.forEach(el => {
        el.removeAttribute("hidden")
    })
    // hides select a mod first option as that is an invalid answer
    document.querySelector('[name="' + "modfirst" + '"]').setAttribute('hidden', 'true');
    document.querySelector('[name="' + "modfirst" + '"]').selected = true;
}

function undisable(id) {
    // gets the value of selected option
    document.getElementById(id).removeAttribute("disabled")
}

function zipfile_mods(modslug, mcversion, modversion, input, verchange) {
    // selects the file
    dataSelect = document.getElementById(input)
    let datas = dataSelect.files
    let data = datas[0]

    if (verchange == "1") {
        // Adds the version number in the file provided to minecraft version and mod version boxes
        filename = data.name
        filenamenumb = filename.replace(/^\D+/g, '')
        filenamenumb = filenamenumb.replace('.jar', '')
        filenamenumb = filenamenumb.replace('.zip', '')
        filenamenumb = filenamenumb.replace(' ', '')
        document.getElementById(mcversion).value = filenamenumb;
        document.getElementById(modversion).value = filenamenumb;
    }

    // gets the values in the text boxes
    modslugname = document.getElementById(modslug).value;
    mcversionname = document.getElementById(mcversion).value;
    modversionname = document.getElementById(modversion).value;

    if (data.type != "application/x-zip-compressed") { // if uploaded file is not a zip file
        // starts a new zipfile
        var zip = new JSZip();
        if (data.type != "application/json" && data.name != "modpack.jar") { // if the file is not modpack.jar or filetye json
            // adds a folder "mods" inside zipfile
            var mods = zip.folder("mods");
            // adds the file uploaded inside mods folder with correct naming scheme
            mods.file(modslugname + "-" + mcversionname + "-" + modversionname + ".jar", data);
            jardataSelect = document.getElementById("jarfile")
            let jarfinalfile = new File([data], modslugname + "-" + mcversionname + "-" + modversionname + ".jar", { type: "application/zip", lastModified: new Date().getTime() });

            let jarcontainer = new DataTransfer();
            jarcontainer.items.add(jarfinalfile);

            jardataSelect.files = jarcontainer.files;

            hashjarmd5("jarfile")

            document.getElementById("filetypejar").checked = true;
        }
        if (data.name == "modpack.jar") { // if the filename is detected to be modpack.jar
            // adds a folder "bin" inside zipfile
            var bin = zip.folder("bin");
            // adds the file uploaded inside bin folder with correct naming scheme
            bin.file("modpack.jar", data);
            document.getElementById('jarmd5').value = "0";
            document.getElementById("filetypelauncher").checked = true;
        }
        if (data.type == "application/json") { // if the filetype is detected to be json
            // adds a folder "bin" inside zipfile
            var bin = zip.folder("bin");
            // adds the file uploaded inside bin folder with correct naming scheme
            bin.file("version.json", data);
            document.getElementById('jarmd5').value = "0";
            document.getElementById("filetypelauncher").checked = true;

        }
        // generates the zipfile
        zip.generateAsync({ type: "blob" })
            .then(function (blob) {
                zipfile_md5(blob)
            });

    }
    if (data.type == "application/x-zip-compressed") { // if the filetype is detected to be zip
        zipfile_md5(data)
        document.getElementById('jarmd5').value = "0";
        document.getElementById("filetypezip").checked = true;
    }
}

function zipfile_md5(file) {
    document.getElementById('md5').value = "";
    // hashes file
    var reader = new FileReader();
    reader.onload = function (event) {
        document.getElementById('md5').value = md5(event.target.result)

    };
    reader.readAsArrayBuffer(file);
    // finds the filesize of file
    document.getElementById('filesize').value = file.size;
    file2 = file;
}


// if download is checked, sends the output to user
function submit_zipfile_mods() {
    if (modslugname != "") {

        if (document.getElementById("downloadzip").checked) {
            saveAs(file2, modslugname + "-" + mcversionname + "-" + modversionname + ".zip");
        }
        // replaces the submitted file with the new zip file generated by previous code from the original file
        let finalfile = new File([file2], modslugname + "-" + mcversionname + "-" + modversionname + ".zip", { type: "application/zip", lastModified: new Date().getTime() });

        let container = new DataTransfer();
        container.items.add(finalfile);

        dataSelect.files = container.files;

        // https://stackoverflow.com/questions/21892890/is-it-possible-to-replace-a-file-input-with-a-blob

        // submits the file using the invisible submit button, so this script can be run and backend see which form was submitted
    }
    submit2 = '[name="' + 'form-submit' + '"]';
    document.querySelector(submit2).click();
}
