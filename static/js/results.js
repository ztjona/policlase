// Resultados: clic en una nota para corregirla a mano (o volver a la automática).
(function () {
  "use strict";
  var token = document.querySelector("input[name=csrfmiddlewaretoken]");
  try {                                            // volver al mismo lugar tras recargar
    var y = sessionStorage.getItem("policlase.results.y");
    if (y) { window.scrollTo(0, Number(y)); sessionStorage.removeItem("policlase.results.y"); }
  } catch (e) {}

  function save(button, data) {
    fetch(button.dataset.url, { method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": token ? token.value : "" }, body: new URLSearchParams(data) })
      .then(function (r) { return r.json().then(function (body) {
        if (!r.ok) { window.alert(body.error || r.status); return; }
        try { sessionStorage.setItem("policlase.results.y", String(window.scrollY)); } catch (e) {}
        location.reload();                          // totales y porcentajes al día
      }); });
  }

  document.addEventListener("click", function (e) {
    var button = e.target.closest("button.grade");
    if (!button || button.closest(".grade-edit")) return;
    document.querySelectorAll(".grade-edit").forEach(function (el) { el.remove(); });
    var box = document.createElement("span");
    box.className = "grade-edit";
    var input = document.createElement("input");
    input.type = "number"; input.min = "0"; input.max = button.dataset.max; input.step = "any";
    input.value = button.dataset.points; input.setAttribute("aria-label", gettext("Nota"));
    var ok = document.createElement("button");
    ok.type = "button"; ok.className = "icon-btn"; ok.textContent = "✓"; ok.title = gettext("Guardar");
    box.append(input, ok);
    if (button.dataset.edited) {
      var auto = document.createElement("button");
      auto.type = "button"; auto.className = "icon-btn"; auto.textContent = "↺"; auto.title = gettext("Volver a la nota automática");
      auto.addEventListener("click", function () { save(button, { restore: "1" }); });
      box.append(auto);
    }
    ok.addEventListener("click", function () { save(button, { points: input.value }); });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") save(button, { points: input.value });
      if (ev.key === "Escape") box.remove();
    });
    button.after(box);
    input.focus(); input.select();
  });
})();
