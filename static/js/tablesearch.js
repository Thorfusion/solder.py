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

function submitbuttonpress(id, val) {
    document.getElementById(id).value = val;
    sleep(25)
    document.querySelector('[name="form2-submit"]').click();
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