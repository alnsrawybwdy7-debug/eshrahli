(function () {
  "use strict";
  var days = document.getElementById("days");
  var picker = document.getElementById("scope-picker");
  document.querySelectorAll('input[name="scope"]').forEach(function (r) {
    r.addEventListener("change", function () {
      picker.hidden = !document.getElementById("scope-subjects").checked;
    });
  });
  document.querySelectorAll("[data-days]").forEach(function (b) {
    b.addEventListener("click", function () { days.value = b.dataset.days; });
  });
})();
