(function () {
  "use strict";
  var root = document.getElementById("live-root");
  if (!root) return;
  var LK = window.LivekitClient;
  var RoomEvent = LK && LK.RoomEvent;
  var Track = LK && LK.Track;

  var isAdmin = root.dataset.isAdmin === "1";
  var $ = function (id) { return document.getElementById(id); };
  var joinBox = $("join-box"), stageBox = $("stage-box"), endedBox = $("ended-box");
  var stage = $("stage"), audioSink = $("audio-sink");
  var btnMic = $("btn-mic"), btnCam = $("btn-cam"), btnScreen = $("btn-screen"), btnLeave = $("btn-leave");
  var peopleEl = $("people"), peopleCount = $("people-count");
  var chatLog = $("chat-log"), chatForm = $("chat-form"), chatInput = $("chat-input"), chatSend = $("chat-send");
  var joinError = $("join-error");

  var room = null;
  var statusTimer = null;
  var tiles = {}; // key -> element
  var encoder = new TextEncoder(), decoder = new TextDecoder();

  function showError(msg) { joinError.textContent = msg; joinError.hidden = false; }

  var KICKED_MSG = "انطلعت من البث من قبل الأستاذة.";
  function kickedOut(msg) {
    endedView(msg || KICKED_MSG);
    setTimeout(function () { location.href = root.dataset.homeUrl; }, 4000);
  }

  // ---------- Teacher moderation ----------
  function moderate(urlTemplate, identity, btn) {
    btn.disabled = true;
    return fetch(urlTemplate.replace("IDENT", encodeURIComponent(identity)), {
      method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": window.CSRF }
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || "صار خطأ، جرّبي مرة ثانية.");
        return d;
      });
    }).catch(function (err) {
      alert(err && err.message ? err.message : String(err));
    }).finally(function () { btn.disabled = false; });
  }

  function modButton(text, danger, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-sm mod" + (danger ? " btn-danger" : "");
    b.textContent = text;
    b.addEventListener("click", onClick);
    return b;
  }

  function roleOf(p) {
    try { return JSON.parse(p.metadata || "{}").role || "student"; } catch (e) { return "student"; }
  }
  function displayName(p) { return p.name || p.identity; }

  // ---------- Tiles ----------
  function tileKey(p, source) { return p.identity + ":" + source; }

  function ensureTile(p, source) {
    var key = tileKey(p, source);
    if (tiles[key]) return tiles[key];
    var el = document.createElement("div");
    el.className = "tile";
    el.dataset.identity = p.identity;
    el.dataset.source = source;
    var name = document.createElement("span");
    name.className = "name";
    name.textContent = displayName(p) + (source === "screen" ? " — الشاشة" : "");
    el.appendChild(name);
    tiles[key] = el;
    stage.appendChild(el);
    layout();
    return el;
  }

  function removeTile(p, source) {
    var key = tileKey(p, source);
    if (tiles[key]) { tiles[key].remove(); delete tiles[key]; layout(); }
  }

  function teacherPlaceholder(p) {
    // Keep a tile for the teacher even when their camera is off
    if (roleOf(p) !== "teacher") return;
    var el = ensureTile(p, "camera");
    if (!el.querySelector("video") && !el.querySelector(".placeholder")) {
      var ph = document.createElement("div");
      ph.className = "placeholder";
      ph.textContent = "الكاميرا مطفية";
      el.appendChild(ph);
    }
  }

  function layout() {
    var list = Object.keys(tiles).map(function (k) { return tiles[k]; });
    // Screen share first, then teacher camera, then the rest
    list.sort(function (a, b) {
      var score = function (t) {
        if (t.dataset.source === "screen") return 0;
        var p = findParticipant(t.dataset.identity);
        return p && roleOf(p) === "teacher" ? 1 : 2;
      };
      return score(a) - score(b);
    });
    list.forEach(function (t, i) {
      t.classList.toggle("main", i === 0 && list.length > 1);
      stage.appendChild(t);
    });
    stage.classList.toggle("has-many", list.length > 1);
  }

  function findParticipant(identity) {
    if (!room) return null;
    if (room.localParticipant.identity === identity) return room.localParticipant;
    return room.remoteParticipants.get(identity) || null;
  }

  function attachVideo(track, publication, p) {
    var source = publication.source === Track.Source.ScreenShare ? "screen" : "camera";
    var el = ensureTile(p, source);
    var ph = el.querySelector(".placeholder");
    if (ph) ph.remove();
    var old = el.querySelector("video");
    if (old) old.remove();
    var v = track.attach();
    v.muted = true; // audio comes via separate elements
    v.playsInline = true;
    el.insertBefore(v, el.firstChild);
  }

  function detachVideo(track, publication, p) {
    track.detach().forEach(function (e) { e.remove(); });
    var source = publication.source === Track.Source.ScreenShare ? "screen" : "camera";
    removeTile(p, source);
    if (source === "camera") teacherPlaceholder(p);
  }

  // ---------- People list ----------
  function renderPeople() {
    if (!room) return;
    var all = [room.localParticipant].concat(Array.from(room.remoteParticipants.values()));
    all.sort(function (a, b) {
      var ra = roleOf(a) === "teacher" ? 0 : 1, rb = roleOf(b) === "teacher" ? 0 : 1;
      return ra - rb || displayName(a).localeCompare(displayName(b), "ar");
    });
    peopleEl.innerHTML = "";
    all.forEach(function (p) {
      var row = document.createElement("div");
      row.className = "p" + (p.isSpeaking ? " speaking" : "");
      var dot = document.createElement("span"); dot.className = "dot";
      var who = document.createElement("span"); who.className = "who";
      who.textContent = displayName(p) + (p.isLocal ? " (أنت)" : "");
      row.appendChild(dot); row.appendChild(who);
      if (!p.isMicrophoneEnabled) {
        var m = document.createElement("span"); m.className = "muted small"; m.textContent = "صامت";
        row.appendChild(m);
      }
      if (isAdmin && !p.isLocal && roleOf(p) !== "teacher") {
        var identity = p.identity, name = displayName(p);
        row.appendChild(modButton("كتم", false, function () {
          moderate(root.dataset.muteUrl, identity, this);
        }));
        row.appendChild(modButton("طرد", true, function () {
          if (!confirm("متأكدة تريدين تطلعين " + name + " من البث؟ ما يكدر يرجع لهذا البث.")) return;
          moderate(root.dataset.kickUrl, identity, this);
        }));
      }
      peopleEl.appendChild(row);
    });
    peopleCount.textContent = String(all.length);
  }

  // ---------- Chat ----------
  // Messages live only in LiveKit (nothing is saved). The teacher can delete one for everyone: a "del"
  // message names it by id (or by sender + text for messages sent without an id, e.g. from the app).
  function newId() { return Math.random().toString(36).slice(2, 12); }

  function addChat(name, text, mine, sender, id) {
    if (chatLog.querySelector(".muted")) chatLog.innerHTML = "";
    var m = document.createElement("div");
    m.className = "m";
    m.dataset.sender = sender || "";
    m.dataset.text = text;
    if (id) m.dataset.id = id;
    var b = document.createElement("b");
    b.textContent = (mine ? "أنت" : name) + ": ";
    m.appendChild(b);
    m.appendChild(document.createTextNode(text));
    if (isAdmin) {
      m.appendChild(modButton("حذف", true, function () {
        if (!room || !confirm("تحذفين هاي الرسالة عند الكل؟")) return;
        var del = { t: "del", sender: m.dataset.sender, text: m.dataset.text };
        if (m.dataset.id) del.id = m.dataset.id;
        room.localParticipant.publishData(encoder.encode(JSON.stringify(del)), { reliable: true, topic: "chat" });
        m.remove();
      }));
    }
    chatLog.appendChild(m);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function deleteChat(del) {
    Array.prototype.forEach.call(chatLog.querySelectorAll(".m"), function (m) {
      var same = del.id ? m.dataset.id === del.id
                        : !m.dataset.id && m.dataset.sender === del.sender && m.dataset.text === del.text;
      if (same) m.remove();
    });
  }

  chatForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = chatInput.value.trim().slice(0, 500);
    if (!text || !room) return;
    var id = newId();
    var payload = encoder.encode(JSON.stringify({ t: "chat", text: text, id: id }));
    room.localParticipant.publishData(payload, { reliable: true, topic: "chat" });
    addChat("", text, true, room.localParticipant.identity, id);
    chatInput.value = "";
  });

  // ---------- Controls ----------
  function setBtn(btn, on, onText, offText) {
    if (!btn) return;
    btn.classList.toggle("on", on);
    btn.textContent = on ? onText : offText;
  }
  function refreshButtons() {
    if (!room) return;
    var lp = room.localParticipant;
    setBtn(btnMic, lp.isMicrophoneEnabled, "إطفاء المايك", "تشغيل المايك");
    setBtn(btnCam, lp.isCameraEnabled, "إطفاء الكاميرا", "تشغيل الكاميرا");
    setBtn(btnScreen, lp.isScreenShareEnabled, "إيقاف مشاركة الشاشة", "مشاركة الشاشة");
  }
  function toggle(fn, current) {
    return function () {
      var btn = this;
      btn.disabled = true;
      fn(!current()).catch(function (err) {
        alert("ما كدرنا نشغلها: " + (err && err.message ? err.message : err));
      }).finally(function () { btn.disabled = false; refreshButtons(); renderPeople(); });
    };
  }

  // ---------- Join / leave ----------
  function endedView(title) {
    stageBox.hidden = true; joinBox.hidden = true; endedBox.hidden = false;
    if (title) $("ended-title").textContent = title;
    chatInput.disabled = true; chatSend.disabled = true;
    if (statusTimer) clearInterval(statusTimer);
  }

  function pollStatus() {
    fetch(root.dataset.statusUrl, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.live || String(d.id) !== root.dataset.sessionId) {
          if (room) room.disconnect();
          endedView("انتهى البث");
        }
      }).catch(function () {});
  }

  async function join() {
    if (!LK) { showError("ما كدرنا نحمّل مكتبة الاتصال. تأكد من الإنترنت وحدّث الصفحة."); return; }
    var btn = $("join-btn");
    btn.disabled = true; btn.textContent = "جاري الاتصال...";
    joinError.hidden = true;
    try {
      var res = await fetch(root.dataset.tokenUrl, {
        method: "POST", credentials: "same-origin",
        headers: { "X-CSRF-Token": window.CSRF }
      });
      var data = await res.json().catch(function () { return {}; });
      if (data.kicked) { kickedOut(data.error); return; }
      if (!res.ok) throw new Error(data.error || "ما كدرنا نجيب صلاحية الدخول.");

      room = new LK.Room({ adaptiveStream: true, dynacast: true });
      wire(room);
      await room.connect(data.url, data.token);
      try { await room.startAudio(); } catch (e) {}

      joinBox.hidden = true; stageBox.hidden = false;
      chatInput.disabled = false; chatSend.disabled = false;

      room.remoteParticipants.forEach(function (p) { teacherPlaceholder(p); });
      if (isAdmin) teacherPlaceholder(room.localParticipant);
      refreshButtons(); renderPeople();
      statusTimer = setInterval(pollStatus, 15000);
    } catch (err) {
      btn.disabled = false; btn.textContent = "انضمام للاتصال";
      showError(err && err.message ? err.message : String(err));
      if (room) { try { room.disconnect(); } catch (e) {} room = null; }
    }
  }

  function wire(r) {
    r.on(RoomEvent.TrackSubscribed, function (track, pub, p) {
      if (track.kind === Track.Kind.Video) attachVideo(track, pub, p);
      else if (track.kind === Track.Kind.Audio) audioSink.appendChild(track.attach());
    });
    r.on(RoomEvent.TrackUnsubscribed, function (track, pub, p) {
      if (track.kind === Track.Kind.Video) detachVideo(track, pub, p);
      else track.detach().forEach(function (e) { e.remove(); });
    });
    r.on(RoomEvent.LocalTrackPublished, function (pub, p) {
      if (pub.track && pub.track.kind === Track.Kind.Video) attachVideo(pub.track, pub, p);
      refreshButtons(); renderPeople();
    });
    r.on(RoomEvent.LocalTrackUnpublished, function (pub, p) {
      if (pub.track && pub.track.kind === Track.Kind.Video) detachVideo(pub.track, pub, p);
      refreshButtons(); renderPeople();
    });
    r.on(RoomEvent.ParticipantConnected, function (p) { teacherPlaceholder(p); renderPeople(); });
    r.on(RoomEvent.ParticipantDisconnected, function (p) {
      removeTile(p, "camera"); removeTile(p, "screen"); renderPeople();
    });
    r.on(RoomEvent.TrackMuted, function () { refreshButtons(); renderPeople(); });
    r.on(RoomEvent.TrackUnmuted, function () { refreshButtons(); renderPeople(); });
    r.on(RoomEvent.ActiveSpeakersChanged, function (speakers) {
      var ids = {};
      speakers.forEach(function (s) { ids[s.identity] = true; });
      Object.keys(tiles).forEach(function (k) {
        tiles[k].classList.toggle("speaking", !!ids[tiles[k].dataset.identity] && tiles[k].dataset.source === "camera");
      });
      renderPeople();
    });
    r.on(RoomEvent.DataReceived, function (payload, p, kind, topic) {
      if (topic && topic !== "chat") return;
      try {
        var msg = JSON.parse(decoder.decode(payload));
        if (msg.t === "chat" && typeof msg.text === "string") {
          addChat(p ? displayName(p) : "؟", msg.text.slice(0, 500), false, p ? p.identity : "",
                  typeof msg.id === "string" ? msg.id.slice(0, 40) : "");
        } else if (msg.t === "del" && p && roleOf(p) === "teacher") {
          deleteChat({ id: typeof msg.id === "string" ? msg.id : "", sender: String(msg.sender || ""),
                       text: String(msg.text || "") });
        }
      } catch (e) {}
    });
    r.on(RoomEvent.Disconnected, function (reason) {
      Object.keys(tiles).forEach(function (k) { tiles[k].remove(); });
      tiles = {};
      audioSink.innerHTML = "";
      var R = LK.DisconnectReason || {};
      room = null;
      if (reason === R.PARTICIPANT_REMOVED && !isAdmin) { kickedOut(); return; }
      if (endedBox.hidden) endedView(reason === R.ROOM_DELETED ? "انتهى البث" : "طلعت من الاتصال");
    });
  }

  $("join-btn").addEventListener("click", join);
  btnMic.addEventListener("click", toggle(function (on) { return room.localParticipant.setMicrophoneEnabled(on); },
    function () { return room.localParticipant.isMicrophoneEnabled; }));
  btnCam.addEventListener("click", toggle(function (on) { return room.localParticipant.setCameraEnabled(on); },
    function () { return room.localParticipant.isCameraEnabled; }));
  if (btnScreen) btnScreen.addEventListener("click", toggle(function (on) {
    return room.localParticipant.setScreenShareEnabled(on, { audio: true });
  }, function () { return room.localParticipant.isScreenShareEnabled; }));
  btnLeave.addEventListener("click", function () { if (room) room.disconnect(); });
  window.addEventListener("beforeunload", function () { if (room) room.disconnect(); });
})();
