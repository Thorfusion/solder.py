document.addEventListener("DOMContentLoaded", function () {
    window.setTimeout(function () {
        document.querySelectorAll("[data-flash-message]").forEach(function (message) {
            bootstrap.Alert.getOrCreateInstance(message).close();
        });
    }, 5000);
});
