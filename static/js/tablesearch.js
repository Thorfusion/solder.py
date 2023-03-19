
var $rows = $('#table #trsearch');
$('#search').keyup(function () {
    var val = $.trim($(this).val()).replace(/ +/g, ' ').toLowerCase();

    $rows.show().filter(function () {
        var text = $(this).text().replace(/\s+/g, ' ').toLowerCase();
        return !~text.indexOf(val);
    }).hide();
});

function buttonpress(id, val){
    document.getElementById(id).value = val;
}
