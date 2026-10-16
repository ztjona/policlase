// Páginas del docente en Actividades: modo de vencimiento, selección múltiple, confirmaciones y
// notas que se guardan en el sitio. Sin JavaScript todo funciona con formularios normales.
(function () {
  "use strict";

  // Vencimiento: solo los campos del modo elegido.
  var settings = document.getElementById("settings");
  if (settings) {
    var show = function (selector, visible) {
      var el = settings.querySelector(selector);
      if (el) el.hidden = !visible;
    };
    var syncDue = function () {
      var mode = (settings.querySelector("input[name=due_mode]:checked") || {}).value;
      show(".due-milestone", mode === "milestone");
      show(".due-opening", mode === "opening");
      show(".due-fixed", mode === "fixed");
      var day = (settings.querySelector("input[name=day_mode]:checked") || {}).value;     // evaluación
      show(".day-milestone", day === "milestone");
      show(".day-fixed", day === "fixed");
      var review = (settings.querySelector("input[name=review_mode]:checked") || {}).value;
      show(".review-milestone", review === "milestone");
      show(".review-fixed", review === "fixed");
      var opens = (settings.querySelector("input[name=open_mode]:checked") || {}).value;    // repaso
      show(".practice-milestone", opens === "milestone");
      show(".practice-fixed", opens === "fixed");
    };
    settings.addEventListener("change", syncDue);
    syncDue();
  }

  // Secciones de la actividad: la casilla del título habilita o deshabilita todos sus ítems.
  document.querySelectorAll("fieldset.part").forEach(function (part) {
    var all = part.querySelector("[data-part-all]");
    if (!all) return;
    var boxes = Array.from(part.querySelectorAll("input[name=enabled]"));
    var sync = function () {
      var on = boxes.filter(function (b) { return b.checked; }).length;
      all.checked = on === boxes.length;
      all.indeterminate = on > 0 && on < boxes.length;
    };
    all.addEventListener("change", function () {
      boxes.forEach(function (b) { b.checked = all.checked; });
      all.form.dispatchEvent(new Event("change", { bubbles: true }));
    });
    boxes.forEach(function (b) { b.addEventListener("change", sync); });
    sync();
  });

  document.querySelectorAll("[data-check-all]").forEach(function (box) {
    box.addEventListener("change", function () {
      var form = box.closest("form");
      form.querySelectorAll('input[type=checkbox][name="' + box.dataset.checkAll + '"]').forEach(function (c) {
        c.checked = box.checked;
      });
    });
  });

  document.addEventListener("click", function (e) {
    var button = e.target.closest("button[data-confirm]");
    if (button && !window.confirm(button.dataset.confirm)) e.preventDefault();
  });

  // Notas por pregunta: se guardan sin recargar, para calificar una respuesta tras otra.
  document.addEventListener("submit", function (e) {
    var form = e.target.closest("[data-grade-form]");
    if (!form) return;
    e.preventDefault();
    var data = new FormData(form);
    if (e.submitter && e.submitter.name) data.set(e.submitter.name, e.submitter.value);
    var status = form.querySelector("[data-grade-status]");
    status.textContent = gettext("Guardando…");
    fetch(form.action, {
      method: "POST", credentials: "same-origin", body: data,
      headers: { "X-CSRFToken": data.get("csrfmiddlewaretoken"), "X-Requested-With": "fetch" },
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.error) { status.textContent = res.error; return; }
      form.querySelector("input[name=points]").value = res.points;
      status.textContent = gettext("calificada");
      var card = form.closest(".pending");
      if (card) card.classList.remove("pending");
    }).catch(function () { status.textContent = gettext("Sin conexión: no se guardó."); });
  });
})();
