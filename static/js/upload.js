(function () {
  "use strict";
  var box = document.getElementById("uploader");
  if (!box) return;
  var form = document.getElementById("upload-form");
  var fileIn = document.getElementById("up-file");
  var titleIn = document.getElementById("up-title");
  var descIn = document.getElementById("up-desc");
  var visIn = document.getElementById("up-visible");
  var btn = document.getElementById("up-btn");
  var prog = document.getElementById("up-progress");
  var bar = prog.querySelector("div");
  var status = document.getElementById("up-status");
  var busy = false;

  function setStatus(t, isErr) {
    status.textContent = t;
    status.style.color = isErr ? "var(--danger)" : "";
  }
  function fmt(n) {
    if (n > 1073741824) return (n / 1073741824).toFixed(2) + " GB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  // Parse a JSON reply; if the server answered with an HTML error page, explain it in Arabic
  async function readJson(res) {
    try { return await res.json(); }
    catch (e) {
      if (res.status === 401 || res.redirected) throw new Error("انتهت الجلسة. سجّل دخول مرة ثانية وحدّث الصفحة.");
      if (res.status === 400) throw new Error("انتهت صلاحية الصفحة. حدّث الصفحة (Ctrl+F5) وجرّب مرة ثانية.");
      throw new Error("صار خطأ بالسيرفر (" + res.status + "). شوف الترمنال حتى تعرف السبب.");
    }
  }

  fileIn.addEventListener("change", function () {
    var f = fileIn.files[0];
    if (f && !titleIn.value) titleIn.placeholder = f.name.replace(/\.[^.]+$/, "");
  });

  window.addEventListener("beforeunload", function (e) {
    if (busy) { e.preventDefault(); e.returnValue = ""; }
  });

  function put(url, headers, file) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("PUT", url, true);
      Object.keys(headers || {}).forEach(function (h) { xhr.setRequestHeader(h, headers[h]); });
      var started = Date.now();
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var pct = (e.loaded / e.total) * 100;
        bar.style.width = pct.toFixed(1) + "%";
        var secs = (Date.now() - started) / 1000;
        var speed = secs > 0 ? e.loaded / secs : 0;
        var eta = speed > 0 ? Math.round((e.total - e.loaded) / speed) : 0;
        setStatus("جاري الرفع " + pct.toFixed(0) + "% — " + fmt(e.loaded) + " من " + fmt(e.total) +
          (eta > 0 ? " — باقي تقريباً " + (eta > 90 ? Math.round(eta / 60) + " دقيقة" : eta + " ثانية") : ""));
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else reject(new Error("فشل الرفع (" + xhr.status + ")"));
      };
      xhr.onerror = function () { reject(new Error("انقطع الاتصال أثناء الرفع. إذا تستخدم R2 تأكد من إعدادات CORS.")); };
      xhr.send(file);
    });
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    var f = fileIn.files[0];
    if (!f || busy) return;
    busy = true; btn.disabled = true; prog.hidden = false; bar.style.width = "0%";
    setStatus("جاري التحضير...");
    try {
      var r = await fetch(box.dataset.url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": window.CSRF },
        body: JSON.stringify({ subject_id: parseInt(box.dataset.subject, 10), kind: box.dataset.kind, filename: f.name, size: f.size })
      });
      var target = await readJson(r);
      if (!r.ok) throw new Error(target.error || "ما كدرنا نبدي الرفع.");

      await put(target.url, target.headers, f);
      setStatus("جاري الحفظ...");

      var body = new URLSearchParams();
      body.set("_csrf", window.CSRF);
      body.set("kind", box.dataset.kind);
      body.set("key", target.key);
      body.set("filename", f.name);
      body.set("size", String(f.size));
      body.set("title", titleIn.value.trim());
      body.set("description", descIn.value.trim());
      body.set("is_visible", visIn.checked ? "1" : "0");
      var c = await fetch(box.dataset.create, { method: "POST", credentials: "same-origin", body: body });
      var res = await readJson(c);
      if (!c.ok) throw new Error(res.error || "ما كدرنا نحفظ الملف.");
      setStatus("تم الرفع ✓");
      busy = false;
      location.reload();
    } catch (err) {
      busy = false; btn.disabled = false;
      setStatus(err.message || String(err), true);
    }
  });
})();
