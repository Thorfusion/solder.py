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

// Thanks https://gist.github.com/effeect/58d50fc7b8db60cf558da183a55eb1ae

function buttonpress(id, val) {
    document.getElementById(id).value = val;
}

function sleep(milliseconds) {
    var start = new Date().getTime();
    for (var i = 0; i < 1e7; i++) {
      if ((new Date().getTime() - start) > milliseconds){
        break;
      }
    }
  }

// https://stackoverflow.com/questions/16873323/javascript-sleep-wait-before-continuing

function submitbuttonpress(id, val, submitid) {
    document.getElementById(id).value = val;
    submit2 = '[name="' + submitid + '"]';
    sleep(25)
    document.querySelector(submit2).click();
}

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

function hashmd5() {
    let fileSelect = document.getElementById('file')
    let files = fileSelect.files
    let file = files[0]

    var reader = new FileReader();
    reader.onload = function (event) {
        document.getElementById('md5').value = md5(event.target.result)
    };
    reader.readAsArrayBuffer(file);
}


function filesizecalc(input) {
    document.getElementById('filesize').value = input.files[0].size;
}


function toslug(slug, string){
    document.getElementById(slug).value = string_to_slug(document.getElementById(string).value);
}

function string_to_slug (str) {
    str = str.replace(/^\s+|\s+$/g, ''); // trim
    str = str.toLowerCase();
  
    // remove accents, swap ñ for n, etc
    var from = "àáãäâèéëêìíïîòóöôùúüûñç·/_,:;";
    var to   = "aaaaaeeeeiiiioooouuuunc------";

    for (var i=0, l=from.length ; i<l ; i++) {
        str = str.replace(new RegExp(from.charAt(i), 'g'), to.charAt(i));
    }

    str = str.replace(/[^a-z0-9 -]/g, '') // remove invalid chars
        .replace(/\s+/g, '-') // collapse whitespace and replace by -
        .replace(/-+/g, '-'); // collapse dashes

    return str;
}

// Thanks https://gist.github.com/codeguy/6684588?permalink_comment_id=3777802
    