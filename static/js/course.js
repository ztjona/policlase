// Pestaña Clases: secciones minimizables, renombrar con doble clic, eliminar con la X y
// arrastrar presentaciones entre secciones (en un curso con GitHub, el archivo cambia de carpeta).
(function () {
  "use strict";
  var root = document.getElementById("units");
  if (!root) return;
  var csrf = root.dataset.csrf;

  function post(url, data) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf },
                        body: new URLSearchParams(data || {}) });
  }

  // Minimizar o desplegar se recuerda sin recargar.
  root.querySelectorAll("details.unit[data-toggle]").forEach(function (d) {
    d.addEventListener("toggle", function () { post(d.dataset.toggle, { collapsed: d.open ? "0" : "1" }); });
  });

  // Doble clic en el título: renombrar en el lugar. Enter o salir del campo guarda; Esc cancela.
  root.querySelectorAll(".unit-title[data-rename]").forEach(function (title) {
    title.addEventListener("dblclick", function (e) {
      e.preventDefault();
      var input = document.createElement("input");
      input.type = "text"; input.value = title.textContent.trim(); input.className = "rename"; input.maxLength = 200;
      title.replaceWith(input);
      input.focus(); input.select();
      var done = false;
      function finish(save) {
        if (done) return;
        done = true;
        var value = input.value.trim();
        if (!save || !value || value === title.textContent.trim()) { input.replaceWith(title); return; }
        input.disabled = true;
        post(title.dataset.rename, { title: value }).then(function () { location.reload(); });
      }
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
        else if (ev.key === "Escape") finish(false);
        else if (ev.key === " ") ev.stopPropagation();     // que el espacio no pliegue la sección
      });
      input.addEventListener("click", function (ev) { ev.preventDefault(); });
      input.addEventListener("blur", function () { finish(true); });
    });
  });

  // X: eliminar una sección vacía.
  root.querySelectorAll("[data-delete]").forEach(function (button) {
    button.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      if (!window.confirm(button.dataset.confirm)) return;
      post(button.dataset.delete).then(function () { location.reload(); });
    });
  });

  // Nueva sección (arriba y abajo): muestra el campo en ese lugar.
  root.querySelectorAll("[data-new-section]").forEach(function (button) {
    button.addEventListener("click", function () {
      var form = button.parentNode.querySelector("form.new-section");
      form.hidden = false;
      button.hidden = true;
      form.querySelector("input").focus();
    });
  });

  // Arrastrar y soltar presentaciones entre secciones.
  var dragged = null;
  root.querySelectorAll(".deck-row[draggable=true]").forEach(function (row) {
    row.addEventListener("dragstart", function (e) {
      dragged = row;
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", row.dataset.move);
    });
    row.addEventListener("dragend", function () {
      row.classList.remove("dragging");
      root.querySelectorAll(".drop-target").forEach(function (z) { z.classList.remove("drop-target"); });
    });
  });
  root.querySelectorAll(".drop-section").forEach(function (zone) {
    zone.addEventListener("dragover", function (e) {
      if (!dragged || dragged.closest(".drop-section") === zone) return;
      e.preventDefault();
      zone.classList.add("drop-target");
      if (zone.tagName === "DETAILS" && !zone.open) zone.open = true;
    });
    zone.addEventListener("dragleave", function (e) {
      if (!zone.contains(e.relatedTarget)) zone.classList.remove("drop-target");
    });
    zone.addEventListener("drop", function (e) {
      if (!dragged) return;
      e.preventDefault();
      var row = dragged;
      dragged = null;
      zone.classList.remove("drop-target");
      zone.querySelector(".unit-body").appendChild(row);          // respuesta inmediata en pantalla
      row.classList.add("moving");
      post(row.dataset.move, { section: zone.dataset.section }).then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) window.alert(data.error || r.status);
          location.reload();
        });
      });
    });
  });
})();
