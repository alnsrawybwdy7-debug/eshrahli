(function () {
  "use strict";
  var meta = document.querySelector('meta[name="csrf-token"]');
  window.CSRF = meta ? meta.content : "";

  // Confirmation for dangerous forms: <form data-confirm="...">
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f && f.dataset && f.dataset.confirm && !window.confirm(f.dataset.confirm)) {
      e.preventDefault();
    }
  }, true);

  // Click-to-copy: <button data-copy="text">
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) return;
    var text = b.dataset.copy;
    var done = function () {
      var old = b.textContent;
      b.textContent = "تم النسخ ✓";
      setTimeout(function () { b.textContent = old; }, 1200);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done);
    } else {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (err) {}
      ta.remove();
    }
  });

  // Live banner polling
  var banner = document.getElementById("live-banner");
  if (banner && banner.dataset.poll) {
    var title = document.getElementById("live-banner-title");
    var poll = function () {
      fetch(banner.dataset.poll, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : { live: false }; })
        .then(function (d) {
          banner.hidden = !d.live;
          if (d.live && title) title.textContent = d.title;
        })
        .catch(function () {});
    };
    setInterval(poll, 30000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  }
})();
