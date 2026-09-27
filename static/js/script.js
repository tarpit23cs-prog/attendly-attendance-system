// Attendly - basic client-side helpers

// Auto-hide alert messages after 4 seconds
document.addEventListener("DOMContentLoaded", function () {
    var alerts = document.querySelectorAll(".alert-auto-hide");
    alerts.forEach(function (alert) {
        setTimeout(function () {
            alert.style.display = "none";
        }, 4000);
    });
});

// Ask for confirmation before deactivating an employee
function confirmDeactivate(name) {
    return confirm("Are you sure you want to change the status of " + name + "?");
}
