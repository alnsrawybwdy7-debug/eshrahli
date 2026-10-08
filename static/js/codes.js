(function () {
  "use strict";
  var days = document.getElementById("days");
  document.querySelectorAll("[data-days]").forEach(function (b) {
    b.addEventListener("click", function () { days.value = b.dataset.days; });
  });
})();
