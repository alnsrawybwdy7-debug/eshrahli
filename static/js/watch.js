(function () {
  "use strict";
  var v = document.getElementById("player");
  var wm = document.getElementById("wm");
  if (!v) return;
  // Discourage casual saving: no context menu on the player
  v.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  // Moving watermark with the student's username (deters screen-recording leaks)
  if (wm) {
    var move = function () {
      wm.style.top = (5 + Math.random() * 80) + "%";
      wm.style.left = (5 + Math.random() * 70) + "%";
    };
    move();
    setInterval(move, 7000);
  }
  // Remember playback position per video
  var key = "pos:" + location.pathname;
  try {
    var saved = parseFloat(localStorage.getItem(key) || "0");
    if (saved > 5) {
      v.addEventListener("loadedmetadata", function () {
        if (saved < v.duration - 10) v.currentTime = saved;
      }, { once: true });
    }
    setInterval(function () {
      if (!v.paused) localStorage.setItem(key, String(v.currentTime));
    }, 5000);
  } catch (e) {}
})();
