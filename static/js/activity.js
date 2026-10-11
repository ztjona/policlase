// Intento de una actividad: autoguardado, imágenes que se suben al elegirlas y señales para el
// docente (pegar texto, salir de la página). Sin JavaScript, el formulario se guarda y se envía
// con sus botones, imágenes incluidas.
(function () {
  "use strict";
  var form = document.getElementById("attempt-form");
  if (!form) return;
  var status = form.querySelector("[data-save-status]");
  var telemetryField = form.querySelector("input[name=telemetry]");
  var delta = { pastes: 0, pasted_chars: 0, blurs: 0, blocked_pastes: 0 };
  var timer = null, dirty = false, saving = false, away = false;

  function csrf() { return form.querySelector("input[name=csrfmiddlewaretoken]").value; }
  function say(text) { if (status) status.textContent = text; }

  // ------------------------------------------------------------------ señales

  form.addEventListener("paste", function (e) {
    var target = e.target;
    if (target.hasAttribute("data-no-paste")) {
      e.preventDefault();
      delta.blocked_pastes += 1;
      say(gettext("En esta respuesta no se puede pegar: escríbala usted."));
      return;
    }
    var text = (e.clipboardData && e.clipboardData.getData("text")) || "";
    delta.pastes += 1;
    delta.pasted_chars += text.length;
  });
  // Salir de la página: cambiar de pestaña o de ventana. Se cuenta una vez por salida.
  function leave() { if (!away) { away = true; delta.blurs += 1; flush(); } }
  function back() { away = false; }
  document.addEventListener("visibilitychange", function () { if (document.hidden) leave(); else back(); });
  window.addEventListener("blur", leave);
  window.addEventListener("focus", back);

  // ------------------------------------------------------------------ autoguardado

  function payload() {
    telemetryField.value = JSON.stringify(delta);
    var data = new FormData(form);
    Array.from(data.keys()).forEach(function (k) { if (k.indexOf("f-") === 0) data.delete(k); });
    data.set("action", "save");
    return data;
  }

  function resetDelta(sent) {
    Object.keys(delta).forEach(function (k) { delta[k] = Math.max(0, delta[k] - (sent[k] || 0)); });
  }

  function save(keepalive) {
    if (saving) { dirty = true; return; }
    saving = true;
    dirty = false;
    var sent = JSON.parse(JSON.stringify(delta));
    say(gettext("Guardando…"));
    return fetch(form.action || location.href, {
      method: "POST", credentials: "same-origin", body: payload(), keepalive: !!keepalive,
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (data) {
      resetDelta(sent);
      say(interpolate(gettext("Guardado a las %s"), [data.saved_at]));
    }).catch(function () {
      dirty = true;
      say(gettext("Sin conexión: no se guardó. Siga escribiendo; se reintentará."));
    }).then(function () {
      saving = false;
      if (dirty) schedule();
    });
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(save, 2500); }
  function flush() { clearTimeout(timer); save(true); }

  form.addEventListener("input", function (e) { if (!e.target.matches("[data-upload]")) { dirty = true; schedule(); } });
  form.addEventListener("change", function (e) { if (!e.target.matches("[data-upload]")) { dirty = true; schedule(); } });
  setInterval(function () { if (delta.pastes || delta.blurs || delta.blocked_pastes) save(); }, 60000);

  // ------------------------------------------------------------------ imágenes

  function box(key) { return form.querySelector('[data-uploads="' + CSS.escape(key) + '"]'); }

  function showError(container, text) {
    var el = container.querySelector("[data-upload-error]");
    if (!el) return;
    el.textContent = text || "";
    el.hidden = !text;
  }

  function upload(input) {
    var key = input.dataset.upload, files = Array.from(input.files || []);
    var container = box(key);
    if (!files.length || !container) return;
    input.disabled = true;
    say(gettext("Subiendo imagen…"));
    var chain = Promise.resolve(), error = "";
    files.forEach(function (file) {
      chain = chain.then(function () {
        if (error) return;
        var data = new FormData();
        data.set("csrfmiddlewaretoken", csrf());
        data.set("key", key);
        data.append("f-" + key, file);
        return fetch(form.dataset.uploadUrl, {
          method: "POST", credentials: "same-origin", body: data,
          headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
        }).then(function (r) { return r.json(); }).then(function (res) {
          if (res.html) container.innerHTML = res.html;
          if (res.error) error = res.error;
        });
      });
    });
    chain.catch(function () { error = gettext("No se pudo subir la imagen. Revise su conexión e intente de nuevo."); })
      .then(function () {
        showError(container, error);
        say(error ? "" : gettext("Imagen guardada."));
      });
  }

  form.addEventListener("change", function (e) {
    if (e.target.matches("[data-upload]")) upload(e.target);
  });

  form.addEventListener("click", function (e) {
    var remove = e.target.closest("[data-remove-upload]");
    if (remove) {
      e.preventDefault();
      var container = remove.closest("[data-uploads]");
      var data = new FormData();
      data.set("csrfmiddlewaretoken", csrf());
      fetch(remove.getAttribute("formaction"), {
        method: "POST", credentials: "same-origin", body: data,
        headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
      }).then(function (r) { return r.json(); }).then(function (res) {
        if (container && res.html !== undefined) container.innerHTML = res.html;
      });
      return;
    }
    var confirmButton = e.target.closest("button[data-confirm]");
    if (confirmButton && !window.confirm(confirmButton.dataset.confirm)) e.preventDefault();
  });

  // Enviar o guardar con el botón: viajan también las señales pendientes.
  form.addEventListener("submit", function () {
    clearTimeout(timer);
    telemetryField.value = JSON.stringify(delta);
  });
})();
