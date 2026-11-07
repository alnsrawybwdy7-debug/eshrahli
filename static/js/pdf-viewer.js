// In-site PDF viewer (pdf.js): pages drawn on canvas, rendered lazily, zoom + pinch, watermark,
// no text layer and no download / print. It only makes copying harder: the bytes reach the browser.
const root = document.getElementById("pdf-root");
if (root) start();

async function start() {
  const $ = (id) => document.getElementById(id);
  const wrap = $("pdf-scroll"), stack = $("pdf-stack"), pagesLabel = $("pdf-pages"), errorBox = $("pdf-error");
  const zoomIn = $("pdf-zoom-in"), zoomOut = $("pdf-zoom-out"), fitBtn = $("pdf-fit"), zoomLevel = $("pdf-zoom-level");
  const wmText = root.dataset.wm || "";

  const MIN_SCALE = 0.3, MAX_SCALE = 4, STEP = 1.2;
  const MAX_CANVAS_PIXELS = 12e6; // stay under mobile canvas limits

  // Keep the toolbar just under the site's sticky top bar
  const topbar = document.querySelector(".topbar"), toolbar = root.querySelector(".pdf-toolbar");
  const stickToolbar = () => { toolbar.style.top = (topbar ? topbar.offsetHeight : 0) + "px"; };
  stickToolbar();
  window.addEventListener("resize", stickToolbar);

  // ---------- no saving / printing / dragging ----------
  document.documentElement.classList.add("no-print");
  root.addEventListener("contextmenu", (e) => e.preventDefault());
  root.addEventListener("dragstart", (e) => e.preventDefault());
  document.addEventListener("keydown", (e) => {
    const k = (e.key || "").toLowerCase();
    if ((e.ctrlKey || e.metaKey) && (k === "s" || k === "p")) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  function fail(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = false;
    pagesLabel.textContent = "";
    wrap.hidden = true;
  }

  // ---------- load ----------
  let pdfjs, doc;
  try {
    pdfjs = await import(root.dataset.lib);
    pdfjs.GlobalWorkerOptions.workerSrc = root.dataset.worker;
  } catch (e) {
    console.error("pdf.js load failed", e);
    fail("ما كدرنا نشغّل العارض. حدّث الصفحة، وإذا تكررت جرّب متصفح ثاني.");
    return;
  }
  try {
    const r = await fetch(root.dataset.sourceUrl, { credentials: "same-origin", cache: "no-store" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.url) throw Object.assign(new Error(d.error || "source " + r.status), { name: "SourceError" });
    doc = await pdfjs.getDocument({
      url: d.url,
      cMapUrl: root.dataset.cmaps,
      cMapPacked: true,
      standardFontDataUrl: root.dataset.fonts,
      isEvalSupported: false, // the site's CSP has no 'unsafe-eval'
      enableXfa: false,
      // One plain GET that starts right away: the signed URL lives only a few minutes,
      // so no Range requests that could come after it expires.
      disableRange: true,
    }).promise;
  } catch (e) {
    console.error("PDF open failed", e);
    if (e && e.name === "PasswordException") fail("هذا الملف محمي بكلمة سر، فما ينعرض هنا. بلّغ الأستاذة حتى ترفع نسخة بدون كلمة سر.");
    else if (e && e.name === "InvalidPDFException") fail("الملف تالف أو مو PDF صالح، فما ينعرض. بلّغ الأستاذة.");
    else fail("ما كدرنا نحمّل الملف. تأكد من الإنترنت وحدّث الصفحة.");
    return;
  }

  // ---------- pages (placeholders sized from page 1, corrected when each page loads) ----------
  const first = await doc.getPage(1);
  const base = first.getViewport({ scale: 1 });
  const pages = [];
  for (let n = 1; n <= doc.numPages; n++) {
    const el = document.createElement("div");
    el.className = "pdf-page";
    el.dataset.n = String(n);
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-label", "صفحة " + n);
    el.appendChild(canvas);
    stack.appendChild(el);
    pages.push({ n, el, canvas, w: base.width, h: base.height, page: n === 1 ? first : null,
                 drawn: 0, task: null, visible: false });
  }

  let scale = 1, fitMode = true;

  function fitScale() {
    const avail = Math.max(200, wrap.clientWidth - 2);
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, avail / base.width));
  }

  function layout() {
    for (const p of pages) {
      p.el.style.width = Math.floor(p.w * scale) + "px";
      p.el.style.height = Math.floor(p.h * scale) + "px";
    }
    zoomLevel.textContent = Math.round(scale * 100) + "%";
    zoomOut.disabled = scale <= MIN_SCALE + 1e-3;
    zoomIn.disabled = scale >= MAX_SCALE - 1e-3;
  }

  function drawWatermark(ctx, w, h) {
    if (!wmText) return;
    ctx.save();
    const size = Math.max(14, Math.round(w / 18));
    ctx.font = "600 " + size + "px system-ui, sans-serif";
    ctx.fillStyle = "rgba(90, 90, 90, 0.16)";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.translate(w / 2, h / 2);
    ctx.rotate(-Math.PI / 6);
    const stepX = ctx.measureText(wmText).width + size * 3, stepY = size * 4.5;
    const reach = Math.hypot(w, h);
    for (let y = -reach; y <= reach; y += stepY) {
      const shift = (Math.round(y / stepY) % 2) * stepX / 2;
      for (let x = -reach; x <= reach; x += stepX) ctx.fillText(wmText, x + shift, y);
    }
    ctx.restore();
  }

  async function render(p) {
    if (p.drawn === scale || !p.visible) return;
    if (p.task) { p.task.cancel(); p.task = null; }
    const want = scale;
    try {
      if (!p.page) {
        p.page = await doc.getPage(p.n);
        const v = p.page.getViewport({ scale: 1 });
        if (v.width !== p.w || v.height !== p.h) { p.w = v.width; p.h = v.height; layout(); }
      }
      const viewport = p.page.getViewport({ scale: want });
      let ratio = Math.min(window.devicePixelRatio || 1, 2);
      const area = viewport.width * viewport.height * ratio * ratio;
      if (area > MAX_CANVAS_PIXELS) ratio *= Math.sqrt(MAX_CANVAS_PIXELS / area);
      const off = document.createElement("canvas");
      off.width = Math.floor(viewport.width * ratio);
      off.height = Math.floor(viewport.height * ratio);
      const ctx = off.getContext("2d");
      p.task = p.page.render({ canvasContext: ctx, viewport,
                               transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null });
      await p.task.promise;
      p.task = null;
      if (want !== scale || !p.visible) return;
      drawWatermark(ctx, off.width, off.height);
      // Swap in the finished canvas so the old one stays until the new one is ready
      off.setAttribute("aria-label", "صفحة " + p.n);
      p.el.replaceChild(off, p.canvas);
      p.canvas = off;
      p.drawn = want;
    } catch (e) {
      p.task = null;
      if (e && e.name === "RenderingCancelledException") return;
      console.error("page " + p.n + " render failed", e);
    }
  }

  function release(p) {
    if (p.task) { p.task.cancel(); p.task = null; }
    if (p.drawn) { p.canvas.width = 0; p.canvas.height = 0; p.drawn = 0; }
  }

  // Render pages near the screen, free the canvases of pages far away (memory on phones)
  const near = new IntersectionObserver((entries) => {
    for (const en of entries) {
      const p = pages[Number(en.target.dataset.n) - 1];
      p.visible = en.isIntersecting;
      if (p.visible) render(p); else release(p);
    }
  }, { rootMargin: "150% 0px" });
  pages.forEach((p) => near.observe(p.el));

  function renderVisible() { pages.forEach((p) => { if (p.visible) render(p); }); }

  // ---------- current page label ----------
  let current = 1;
  function updateCurrent() {
    const mid = window.innerHeight / 2;
    let best = current, bestDist = Infinity;
    for (const p of pages) {
      if (!p.visible) continue;
      const r = p.el.getBoundingClientRect();
      const d = r.top <= mid && r.bottom >= mid ? 0 : Math.min(Math.abs(r.top - mid), Math.abs(r.bottom - mid));
      if (d < bestDist) { bestDist = d; best = p.n; }
    }
    current = best;
    pagesLabel.textContent = "صفحة " + current + " من " + doc.numPages;
  }
  let ticking = false;
  window.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { ticking = false; updateCurrent(); });
  }, { passive: true });

  // ---------- zoom (keeps the page you are reading in place) ----------
  function setScale(next, keepFit) {
    next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
    fitMode = !!keepFit;
    if (Math.abs(next - scale) < 1e-3) { layout(); return; }
    const p = pages[current - 1];
    const r = p.el.getBoundingClientRect();
    const frac = r.height ? (window.innerHeight / 2 - r.top) / r.height : 0;
    scale = next;
    layout();
    const r2 = p.el.getBoundingClientRect();
    window.scrollBy(0, r2.top + frac * r2.height - window.innerHeight / 2);
    renderVisible();
    updateCurrent();
  }

  zoomIn.addEventListener("click", () => setScale(scale * STEP));
  zoomOut.addEventListener("click", () => setScale(scale / STEP));
  fitBtn.addEventListener("click", () => setScale(fitScale(), true));
  window.addEventListener("resize", () => { if (fitMode) setScale(fitScale(), true); });

  // Ctrl + wheel / trackpad pinch on computers
  stack.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setScale(scale * Math.exp(-e.deltaY / 300));
  }, { passive: false });

  // Two-finger pinch on phones: preview with a CSS transform, re-render on release
  let pinch = null;
  const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  stack.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 2) return;
    const rect = stack.getBoundingClientRect();
    const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left;
    const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top;
    pinch = { d0: dist(e.touches), factor: 1 };
    stack.style.transformOrigin = cx + "px " + cy + "px";
  }, { passive: true });
  stack.addEventListener("touchmove", (e) => {
    if (!pinch || e.touches.length !== 2) return;
    e.preventDefault();
    const f = dist(e.touches) / pinch.d0;
    pinch.factor = Math.min(MAX_SCALE / scale, Math.max(MIN_SCALE / scale, f));
    stack.style.transform = "scale(" + pinch.factor + ")";
  }, { passive: false });
  const endPinch = () => {
    if (!pinch) return;
    const f = pinch.factor;
    pinch = null;
    stack.style.transform = "";
    if (Math.abs(f - 1) > 0.02) setScale(scale * f);
  };
  stack.addEventListener("touchend", endPinch);
  stack.addEventListener("touchcancel", endPinch);

  // ---------- go ----------
  scale = fitScale();
  layout();
  fitBtn.disabled = false;
  updateCurrent();
}
