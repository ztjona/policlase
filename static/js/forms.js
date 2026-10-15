// Ayudas de formulario comunes a todas las páginas.
(function () {
  "use strict";

  // Parcial y fila de la ponderación (grades.forms.PeriodRowMixin): la lista de filas muestra solo
  // las del parcial elegido, y el parcial sigue a la fecha (de entrega o del registro) como lo hace
  // gradebook.period_for: el primero que no había cerrado ese día. Sin las fechas de cierre de los
  // parciales, no se adivina.
  document.querySelectorAll("select[data-period]").forEach(function (period) {
    var form = period.form, rows = form && form.querySelector("select[data-rows]");
    if (!rows) return;
    var options = Array.prototype.slice.call(rows.querySelectorAll("option[data-period]"));
    var empty = rows.querySelector("option[value='']");
    var ends = Array.prototype.map.call(period.options, function (o) { return { value: o.value, ends: o.dataset.ends || "" }; });

    function fill() {
      var current = rows.selectedOptions[0], name = current && current.value ? current.textContent.trim() : "";
      while (rows.firstChild) rows.removeChild(rows.firstChild);
      if (empty) rows.appendChild(empty);
      var twin = null;
      options.forEach(function (o) {
        if (o.dataset.period !== period.value) return;
        rows.appendChild(o);
        if (!twin && name && o.textContent.trim() === name) twin = o;
      });
      rows.value = twin ? twin.value : "";
    }

    function day() {
      var mode = form.querySelector("input[name=due_mode]:checked");
      if (mode && mode.value === "none") return "";
      if (mode && mode.value === "milestone") {
        var milestone = form.querySelector("select[name=due_milestone]"), chosen = milestone && milestone.selectedOptions[0];
        if (!chosen || !chosen.dataset.day) return "";
        var d = new Date(chosen.dataset.day + "T12:00:00");
        var offset = form.querySelector("[name=due_offset_days]");
        d.setDate(d.getDate() + Number(offset && offset.value || 0));
        return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
      }
      if (mode && mode.value === "opening") {
        var opens = form.querySelector("[name=opens_at]"), days = form.querySelector("[name=opening_days]");
        if (!opens || !opens.value) return "";
        var o = new Date(opens.value.slice(0, 10) + "T12:00:00");
        o.setDate(o.getDate() + Number(days && days.value || 0));
        return o.getFullYear() + "-" + String(o.getMonth() + 1).padStart(2, "0") + "-" + String(o.getDate()).padStart(2, "0");
      }
      var input = form.querySelector("[name=due_at]") || form.querySelector("[name=on]");
      return input && input.value ? input.value.slice(0, 10) : "";
    }

    function follow() {
      var when = day();
      if (!when || ends.slice(0, -1).some(function (e) { return !e.ends; })) return;
      var pick = ends.filter(function (e) { return !e.ends || when <= e.ends; })[0] || ends[ends.length - 1];
      if (pick && pick.value !== period.value) { period.value = pick.value; fill(); }
    }

    period.addEventListener("change", fill);
    form.addEventListener("change", function (e) { if (e.target !== period && e.target !== rows) follow(); });
    form.addEventListener("input", function (e) { if (e.target.name === "due_offset_days" || e.target.name === "opening_days") follow(); });
    fill();
  });

  // Formularios con data-dirty: lo marcado con data-dirty-enable («Guardar», «Cancelar») se habilita
  // solo cuando hay cambios sin guardar, y salir de la página con cambios pide confirmación.
  // data-dirty="1" marca un formulario que ya llega con cambios pendientes (el servidor lo devolvió
  // con errores, o tras procesar una lista pegada). data-dirty-ignore excluye un campo de la cuenta.
  // Una página que cambia campos por código avisa con el evento «dirty-check» sobre el formulario.
  function snapshot(form) {
    var out = [];
    new FormData(form).forEach(function (value, key) {
      if (key === "csrfmiddlewaretoken") return;
      var field = form.elements[key];
      if (field && field.hasAttribute && field.hasAttribute("data-dirty-ignore")) return;
      out.push(key + "=" + (typeof value === "string" ? value.replace(/\r\n/g, "\n") : value.name + ":" + value.size));
    });
    return out.join("&");
  }

  document.querySelectorAll("form[data-dirty]").forEach(function (form) {
    var start = snapshot(form), forced = form.dataset.dirty === "1", leaving = false;
    var controls = Array.prototype.slice.call(form.querySelectorAll("[data-dirty-enable]"));

    function dirty() { return forced || snapshot(form) !== start; }
    function update() {
      var on = dirty();
      controls.forEach(function (c) {
        if (c.tagName === "A") {
          c.classList.toggle("disabled", !on);
          if (on) { c.removeAttribute("aria-disabled"); c.removeAttribute("tabindex"); }
          else { c.setAttribute("aria-disabled", "true"); c.setAttribute("tabindex", "-1"); }
        } else if (!c.classList.contains("sr-only")) {
          c.disabled = !on;
        }
      });
    }

    controls.forEach(function (c) {
      if (c.tagName === "A") c.addEventListener("click", function (e) { if (c.getAttribute("aria-disabled")) e.preventDefault(); });
    });
    // Enter sin cambios no envía nada (el botón por omisión está oculto y no se deshabilita).
    form.addEventListener("submit", function (e) {
      var by = e.submitter;
      if (by && by.hasAttribute("data-dirty-enable") && !dirty()) { e.preventDefault(); return; }
      leaving = true;
    });
    ["input", "change", "dirty-check"].forEach(function (name) { form.addEventListener(name, update); });
    window.addEventListener("beforeunload", function (e) {
      if (!leaving && dirty()) { e.preventDefault(); e.returnValue = ""; }
    });
    update();
  });
})();
