// Páginas del docente en Actividades: modo de vencimiento, selección múltiple, confirmaciones y
// notas que se guardan en el sitio. Sin JavaScript todo funciona con formularios normales.
(function () {
  "use strict";

  // Vencimiento: solo los campos del modo elegido.
  var settings = document.getElementById("settings");
  if (settings) {
    var syncDue = function () {
      var mode = (settings.querySelector("input[name=due_mode]:checked") || {}).value;
      settings.querySelector(".due-milestone").hidden = mode !== "milestone";
      settings.querySelector(".due-fixed").hidden = mode !== "fixed";
    };
    settings.addEventListener("change", syncDue);
    syncDue();
  }

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
