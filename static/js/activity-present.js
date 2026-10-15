// Presentar una actividad en clase: el docente avanza por el contenido y los ítems, y cada cambio se
// avisa al servidor para que los estudiantes puedan ir a esa diapositiva o seguirla (activity.js).
// Mientras la página está abierta, se envía una señal cada 20 s; sin ella, la presentación se da por
// terminada. Con un control de presentación funcionan las mismas teclas que en las clases en vivo.
(function () {
  "use strict";
  var shell = document.getElementById("shell");
  if (!shell) return;
  var url = shell.dataset.actionUrl;
  var slides = Array.from(document.querySelectorAll(".present-slide"));
  var scroller = document.getElementById("slide-scroll");
  var position = document.querySelector("[data-position]");
  var progressBox = document.querySelector("[data-progress]");
  var conn = document.getElementById("conn");
  var csrf = document.querySelector("input[name=csrfmiddlewaretoken]").value;
  var current = 0, beat = null;

  function post(data, keepalive) {
    var body = new FormData();
    body.set("csrfmiddlewaretoken", csrf);
    Object.keys(data).forEach(function (k) { body.set(k, data[k]); });
    return fetch(url, {
      method: "POST", credentials: "same-origin", body: body, keepalive: !!keepalive,
      headers: { "X-CSRFToken": csrf, "X-Requested-With": "fetch" },
    });
  }

  function showProgress(res) {
    if (!progressBox) return;
    var keys = Object.keys(res.answered || {});
    if (!keys.length || !res.working) { progressBox.textContent = ""; return; }
    var most = Math.max.apply(null, keys.map(function (k) { return res.answered[k]; }));
    progressBox.textContent = interpolate(gettext("Respondieron esta parte: %(n)s de %(total)s en curso"),
                                          { n: most, total: res.working }, true);
  }

  function announce() {
    var slide = slides[current];
    return post({ action: "move", at: slide.dataset.at }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (res) {
      conn.textContent = gettext("los estudiantes ven su posición");
      conn.className = "conn on";
      showProgress(res);
    }).catch(function () {
      conn.textContent = gettext("sin conexión");
      conn.className = "conn off";
    });
  }

  function show(index) {
    current = Math.max(0, Math.min(slides.length - 1, index));
    slides.forEach(function (s, i) { s.hidden = i !== current; });
    scroller.scrollTop = 0;
    position.textContent = (current + 1) + " / " + slides.length;
    var at = slides[current].dataset.at;
    document.querySelectorAll("[data-go]").forEach(function (b) {
      var here = b.dataset.go === at;
      b.closest(".thumb").classList.toggle("current", here);
      if (here) b.scrollIntoView({ block: "nearest" });
    });
    history.replaceState(null, "", "#d" + at);      // sin un id igual: el navegador no desplaza la página
    announce();
    clearInterval(beat);
    beat = setInterval(announce, 20000);
  }

  document.querySelector("[data-next]").addEventListener("click", function () { show(current + 1); });
  document.querySelector("[data-prev]").addEventListener("click", function () { show(current - 1); });
  document.querySelectorAll("[data-go]").forEach(function (b) {
    b.addEventListener("click", function () {
      var i = slides.findIndex(function (s) { return s.dataset.at === b.dataset.go; });
      if (i >= 0) show(i);
    });
  });

  var FORWARD = ["PageDown", "ArrowRight", " ", "Enter"];
  var BACK = ["PageUp", "ArrowLeft"];
  var blank = null;
  function toggleBlank() {
    if (!blank) {
      blank = document.createElement("div");
      blank.className = "blank-screen";
      blank.addEventListener("click", toggleBlank);
      document.body.appendChild(blank);
    } else { blank.remove(); blank = null; }
  }
  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
  }
  document.addEventListener("keydown", function (e) {
    if (e.target.closest("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest("button") && (e.key === " " || e.key === "Enter")) return;
    if (FORWARD.indexOf(e.key) >= 0) { show(current + 1); e.preventDefault(); }
    else if (BACK.indexOf(e.key) >= 0) { show(current - 1); e.preventDefault(); }
    else if (e.key === "b" || e.key === "B" || e.key === ".") { toggleBlank(); e.preventDefault(); }
    else if (e.key === "F5") { toggleFullscreen(); e.preventDefault(); }
  });
  document.getElementById("toggle-fullscreen").addEventListener("click", toggleFullscreen);
  var toggle = document.getElementById("toggle-outline");
  toggle.addEventListener("click", function () {
    var on = !shell.classList.contains("show-outline");
    shell.classList.toggle("show-outline", on);
    toggle.setAttribute("aria-pressed", on ? "true" : "false");
  });

  // Terminar: los estudiantes dejan de ver la posición. Al cerrar la pestaña, lo mismo (si el aviso
  // no llega, la falta de señal la termina en 90 s).
  var stop = document.querySelector("[data-stop]");
  stop.addEventListener("click", function (e) {
    e.preventDefault();
    clearInterval(beat);
    post({ action: "stop" }).finally(function () { location.href = stop.href; });
  });
  window.addEventListener("pagehide", function () { clearInterval(beat); post({ action: "stop" }, true); });

  var start = slides.findIndex(function (s) { return "#d" + s.dataset.at === location.hash; });
  show(start >= 0 ? start : 0);
})();
