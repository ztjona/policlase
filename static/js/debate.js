// El foro del debate (templates/activities/_debate.html). Todo funciona sin JavaScript; con él, el
// «me gusta» se da sin recargar y muestra el número al momento.
(function () {
  "use strict";

  document.addEventListener("click", function (e) {
    var button = e.target.closest("button[data-confirm]");
    if (button && !window.confirm(button.dataset.confirm)) e.preventDefault();
  });

  function token() {
    var field = document.querySelector("input[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  // Justificaciones y comentarios del debate: la vista previa la hace el servidor, con el mismo
  // markdown con que los verán los demás (tablas, negrita, listas y fórmulas).
  function mdPreview(area) {
    var box = document.querySelector('[data-md-box="' + CSS.escape(area.id) + '"]');
    if (!box) return;
    clearTimeout(area.mdTimer);
    if (!area.value.trim()) { box.hidden = true; return; }
    area.mdTimer = setTimeout(function () {
      var data = new FormData();
      data.set("text", area.value);
      fetch(area.dataset.mdPreview, {
        method: "POST", body: data, credentials: "same-origin",
        headers: { "X-CSRFToken": token(), "X-Requested-With": "fetch" },
      }).then(function (r) { return r.json(); }).then(function (res) {
        var html = box.querySelector("[data-md-html]");
        html.innerHTML = res.html;
        box.hidden = false;
        if (window.policlaseMath) window.policlaseMath(html);
      }).catch(function () {});
    }, 400);
  }
  document.addEventListener("input", function (e) {
    if (e.target.matches("textarea[data-md-preview]")) mdPreview(e.target);
  });
  document.querySelectorAll("textarea[data-md-preview]").forEach(function (area) { if (area.value.trim()) mdPreview(area); });

  document.addEventListener("submit", function (e) {
    var form = e.target.closest("form[data-like]");
    if (!form) return;
    e.preventDefault();
    var button = form.querySelector("button");
    if (button.disabled) return;
    button.disabled = true;
    // getAttribute: `form.action` lo taparía un campo llamado «action».
    fetch(form.getAttribute("action"), {
      method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    }).then(function (res) {
      if (!res.ok) { window.alert(res.data.error || gettext("No se pudo registrar el «me gusta».")); return; }
      button.classList.toggle("on", res.data.liked);
      button.setAttribute("aria-pressed", res.data.liked ? "true" : "false");
      button.querySelector("[data-count]").textContent = res.data.count;
    }).catch(function () {
      window.alert(gettext("Sin conexión. Inténtelo de nuevo."));
    }).finally(function () { button.disabled = false; });
  });
})();
