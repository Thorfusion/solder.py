
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

function closesearchabledropdown(menu) {
    if (!menu) {
        return;
    }
    menu.hidden = true;
    menu.classList.remove("show");
    const toggle = menu.parentElement.querySelector('[aria-expanded="true"]');
    if (toggle) {
        toggle.setAttribute("aria-expanded", "false");
    }
}

function togglesearchabledropdown(event, menuId, searchId) {
    event.preventDefault();
    event.stopPropagation();
    const menu = document.getElementById(menuId);
    const search = document.getElementById(searchId);
    if (!menu || !search) {
        return;
    }

    const shouldOpen = menu.hidden;
    document.querySelectorAll('[data-searchable-menu]').forEach(item => {
        closesearchabledropdown(item);
    });
    if (!shouldOpen) {
        return;
    }

    menu.hidden = false;
    menu.classList.add("show");
    event.currentTarget.setAttribute("aria-expanded", "true");
    window.setTimeout(() => {
        search.focus();
        search.select();
    }, 0);
}

function filtersearchabledropdown(inputId, optionsId, noResultsId) {
    const input = document.getElementById(inputId);
    const options = document.getElementById(optionsId);
    const noResults = document.getElementById(noResultsId);
    if (!input || !options || !noResults) {
        return;
    }

    const filter = input.value.trim().toUpperCase();
    let matches = 0;
    options.querySelectorAll('[data-searchable-option]').forEach(option => {
        const text = option.textContent || "";
        option.hidden = !text.toUpperCase().includes(filter);
        if (!option.hidden) {
            matches += 1;
        }
    });
    noResults.hidden = matches !== 0;
}

function selectsearchabledropdown(
    option,
    valueId,
    labelId,
    toggleId,
    menuId,
    submitId
) {
    const value = document.getElementById(valueId);
    const label = document.getElementById(labelId);
    const toggle = document.getElementById(toggleId);
    const submit = submitId ? document.getElementById(submitId) : null;
    if (!option || !value || !label || !toggle || (submitId && !submit)) {
        return false;
    }
    value.value = option.dataset.value;
    label.textContent = option.textContent.trim();
    option.parentElement.querySelectorAll('[data-searchable-option]').forEach(item => {
        item.setAttribute("aria-selected", item === option ? "true" : "false");
    });
    if (submit) {
        submit.disabled = false;
    }
    closesearchabledropdown(document.getElementById(menuId));
    toggle.focus();
    return true;
}

document.addEventListener("click", event => {
    document.querySelectorAll('[data-searchable-menu]').forEach(menu => {
        if (!menu.parentElement.contains(event.target)) {
            closesearchabledropdown(menu);
        }
    });
});

document.addEventListener("keydown", event => {
    if (event.key !== "Escape") {
        return;
    }
    document.querySelectorAll('[data-searchable-menu]').forEach(menu => {
        if (!menu.hidden) {
            const toggle = menu.parentElement.querySelector("button");
            closesearchabledropdown(menu);
            if (toggle) {
                toggle.focus();
            }
        }
    });
});


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

function submitbuttonpresswithurl(version, name, urlform, submitid) {
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

function selectbuildmod(option, valueId, labelId, toggleId, menuId, submitId) {
    if (!selectsearchabledropdown(
        option,
        valueId,
        labelId,
        toggleId,
        menuId,
        submitId
    )) {
        return;
    }

    const versionSelect = document.getElementById("modversion");
    const selected = option.dataset.value;

    versionSelect.querySelectorAll('[data-integration-version]').forEach(option => {
        option.remove();
    });
    versionSelect.removeAttribute("data-integration-loaded");

    versionSelect.querySelectorAll('[name="modlist"]').forEach(option => {
        option.hidden = option.id !== "modversion_" + selected;
    });

    const placeholder = versionSelect.querySelector('[name="modfirst"]');
    placeholder.hidden = true;
    placeholder.selected = true;

    if (option.dataset.integrationUrl) {
        loadintegrationversions("modversion", option.dataset.integrationUrl);
    }
}

function integrationversionlabel(version) {
    let label = version.version;
    if (version.name && version.name !== version.version) {
        label += " - " + version.name;
    }
    if (version.loaders && version.loaders.length) {
        label += " (" + version.loaders.join(", ") + ")";
    }
    return label;
}

async function loadintegrationversions(selectId, endpoint) {
    const select = document.getElementById(selectId);
    if (!select || !endpoint || select.dataset.integrationLoaded === endpoint) {
        return;
    }
    select.querySelectorAll('[data-integration-version="status"]').forEach(option => {
        option.remove();
    });
    select.dataset.integrationLoaded = endpoint;

    const status = document.createElement("option");
    status.disabled = true;
    status.textContent = "Loading provider versions...";
    status.setAttribute("data-integration-version", "status");
    select.appendChild(status);

    try {
        const response = await fetch(endpoint, {
            headers: {"Accept": "application/json"}
        });
        const payload = await response.json();
        status.remove();
        if (!response.ok) {
            throw new Error(payload.error || "Unable to load provider versions.");
        }

        if (!payload.versions.length) {
            status.textContent = "No additional compatible versions";
            select.appendChild(status);
            return;
        }

        payload.versions.forEach(version => {
            const option = document.createElement("option");
            option.value = "integration:" + version.id;
            option.textContent = integrationversionlabel(version);
            option.setAttribute("data-integration-version", version.id);
            select.appendChild(option);
        });
    } catch (error) {
        status.textContent = error.message || "Unable to load provider versions.";
        if (!status.parentNode) {
            select.appendChild(status);
        }
        select.removeAttribute("data-integration-loaded");
    }
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
    let lowerName = data.name.toLowerCase();
    let isZip = lowerName.endsWith(".zip");
    let isJson = lowerName.endsWith(".json") || data.type == "application/json";

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

    if (!isZip) { // Browsers report several different MIME types for ZIP files.
        // starts a new zipfile
        var zip = new JSZip();
        if (!isJson && data.name != "modpack.jar") { // if the file is not modpack.jar or filetype json

            hashjarmd5("file")
            // adds a folder "mods" inside zipfile
            var mods = zip.folder("mods");
            // adds the file uploaded inside mods folder with correct naming scheme
            mods.file(modslugname + "-" + mcversionname + "-" + modversionname + ".jar", data);

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
        if (isJson) { // if the filetype is detected to be json
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
    if (isZip) { // if the filetype is detected to be zip
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
