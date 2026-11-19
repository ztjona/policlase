// Una evaluación que se abre grupo por grupo (`Activity.open_by_group`): mientras el estudiante espera,
// la página pregunta cada pocos segundos si el docente ya la abrió para su grupo y, cuando sí, se
// recarga para mostrar «Comenzar». Con la pestaña oculta no pregunta; al volver, pregunta enseguida.
(function () {
  "use strict";
  var box = document.querySelector("[data-group-wait]");
  if (!box) return;
  var url = box.getAttribute("data-group-wait");
  var EVERY = 5000, timer = null, busy = false;

  function later(ms) {
    clearTimeout(timer);
    timer = setTimeout(check, ms);
  }

  function check() {
    if (busy) return;
    if (document.hidden) { later(EVERY); return; }
    busy = true;
    fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        busy = false;
        if (data && data.open) { location.reload(); return; }
        later(EVERY);
      })
      .catch(function () { busy = false; later(2 * EVERY); });
  }

  later(EVERY);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) later(0); });
})();
