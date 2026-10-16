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
      var debate = (settings.querySelector("input[name=debate_mode]:checked") || {}).value;
      show(".debate-fixed", debate === "fixed");
      var opens = (settings.querySelector("input[name=open_mode]:checked") || {}).value;    // repaso
      show(".practice-milestone", opens === "milestone");
      show(".practice-fixed", opens === "fixed");
    };
    settings.addEventListener("change", syncDue);
    syncDue();
  }

  // ------------------------------------------------------------------ calificación asistida por IA
  // En la actividad: se piden las respuestas por calificar y luego una propuesta por cada una, de a
  // tres a la vez, mostrando el avance. En «Revisar por pregunta»: una propuesta llena el formulario
  // de nota de su respuesta, que el docente revisa y guarda.
  function postForm(url, data) {
    return fetch(url, { method: "POST", body: data, credentials: "same-origin",
                        headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json().then(function (res) { return { ok: r.ok, res: res }; }); });
  }
  var batch = document.querySelector("form[data-ai-batch]");
  if (batch) batch.addEventListener("submit", function (e) {
    e.preventDefault();
    var button = batch.querySelector("button"), status = batch.querySelector("[data-ai-status]");
    var token = batch.querySelector("input[name=csrfmiddlewaretoken]").value;
    button.disabled = true;
    status.textContent = gettext("Buscando las respuestas por calificar…");
    postForm(batch.getAttribute("action"), new FormData(batch)).then(function (answer) {
      var ids = answer.res.answers || [], total = ids.length, next = 0, done = 0, failed = 0, error = "";
      if (!total) { status.textContent = gettext("No hay respuestas que esperen una propuesta."); button.disabled = false; return; }
      function show() {
        status.textContent = interpolate(gettext("%s de %s propuestas generadas"), [done, total]) +
          (failed ? " · " + interpolate(gettext("%s con error"), [failed]) + (error ? ": " + error : "") : "");
      }
      function worker() {
        if (next >= total) return Promise.resolve();
        var id = ids[next++], data = new FormData();
        data.set("csrfmiddlewaretoken", token);
        return postForm(batch.dataset.proposeUrl.replace(/\/0\/$/, "/" + id + "/"), data)
          .then(function (r) { if (r.ok) done += 1; else { failed += 1; error = r.res.error || error; } })
          .catch(function () { failed += 1; })
          .then(function () { show(); return worker(); });
      }
      show();
      Promise.all([worker(), worker(), worker()]).then(function () {
        status.textContent += " · " + gettext("Listo. Revíselas en «Revisar por pregunta».");
        button.disabled = false;
        setTimeout(function () { location.hash = "ai"; location.reload(); }, 2500);
      });
    }).catch(function () { status.textContent = gettext("Sin conexión. Inténtelo de nuevo."); button.disabled = false; });
  });

  document.addEventListener("submit", function (e) {
    var form = e.target.closest("form[data-ai-propose]");
    if (!form) return;
    e.preventDefault();
    var card = form.closest("section"), status = form.querySelector("[data-ai-status]"), button = form.querySelector("button");
    button.disabled = true;
    status.textContent = gettext("Consultando a la IA…");
    postForm(form.getAttribute("action"), new FormData(form)).then(function (r) {
      button.disabled = false;
      if (!r.ok) { status.textContent = r.res.error || gettext("No se pudo generar la propuesta."); return; }
      var grade = card.querySelector("[data-grade-form]");
      grade.querySelector("input[name=points]").value = r.res.points;
      grade.querySelector("[name=comment]").value = r.res.feedback;
      var list = card.querySelector("[data-ai-criteria]");
      list.textContent = "";
      (r.res.criteria || []).forEach(function (c) {
        var li = document.createElement("li"), strong = document.createElement("strong");
        strong.textContent = c.points + " / " + c.max;
        li.appendChild(strong);
        li.appendChild(document.createTextNode(" · " + c.criterion + (c.reason ? " — " + c.reason : "")));
        list.appendChild(li);
      });
      status.textContent = gettext("La propuesta está lista. Revise la nota y el comentario, y guárdelos.");
    }).catch(function () { button.disabled = false; status.textContent = gettext("Sin conexión. Inténtelo de nuevo."); });
  });

  // Revisar imágenes: «Ver las imágenes completas» se recuerda en este navegador.
  var fullImages = document.querySelector("[data-full-images]");
  if (fullImages) {
    try { fullImages.checked = localStorage.getItem("policlase.fullImages") === "1"; } catch (e) {}
    fullImages.addEventListener("change", function () {
      try { localStorage.setItem("policlase.fullImages", fullImages.checked ? "1" : "0"); } catch (e) {}
    });
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
