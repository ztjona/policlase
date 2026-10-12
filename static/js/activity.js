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
      say(gettext("En esta respuesta no está permitido pegar texto."));
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
      say(gettext("Sin conexión: no se guardó. Puede seguir escribiendo; se volverá a intentar automáticamente."));
    }).then(function () {
      saving = false;
      if (dirty) schedule();
    });
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(save, 2500); }
  function flush() { clearTimeout(timer); save(true); }

  // Respuestas abiertas: las fórmulas $…$ y $$…$$ se ven renderizadas debajo, como las verá el docente.
  var MATH = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g;
  function escapeHtml(t) {
    return t.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }
  function mathPreview(area) {
    var box = form.querySelector('[data-math-preview="' + CSS.escape(area.id) + '"]');
    if (!box || !window.katex) return;
    var text = area.value, html = "", last = 0, any = false;
    text.replace(MATH, function (all, block, inline, offset) {
      any = true;
      html += escapeHtml(text.slice(last, offset)).replace(/\n/g, "<br>");
      html += window.katex.renderToString(block !== undefined ? block : inline,
                                          { displayMode: block !== undefined, throwOnError: false });
      last = offset + all.length;
    });
    box.hidden = !any;
    box.innerHTML = any ? html + escapeHtml(text.slice(last)).replace(/\n/g, "<br>") : "";
  }
  form.querySelectorAll("textarea[data-open]").forEach(function (area) {
    area.addEventListener("input", function () { mathPreview(area); });
    window.addEventListener("load", function () { mathPreview(area); });
  });

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

  // ------------------------------------------------------------------ preguntas con tiempo
  // El reloj es el del servidor: aquí solo se muestra la cuenta regresiva y, al llegar a cero, se
  // envía lo escrito (el servidor acepta unos segundos de gracia por la conexión).

  function replaceTimed(block, html) {
    var holder = document.createElement("div");
    holder.innerHTML = html;
    var fresh = holder.firstElementChild;
    block.replaceWith(fresh);
    if (window.policlaseMath) window.policlaseMath(fresh);
    startTimer(fresh);
    return fresh;
  }

  function timerAction(block, url) {
    if (block.dataset.busy) return;
    block.dataset.busy = "1";
    var data = payload();
    return fetch(url, {
      method: "POST", credentials: "same-origin", body: data,
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.html) replaceTimed(block, res.html);
      else { delete block.dataset.busy; if (res.error) say(res.error); }
    }).catch(function () {
      delete block.dataset.busy;
      say(gettext("Sin conexión: no se pudo enviar. Se volverá a intentar automáticamente."));
      setTimeout(function () { timerAction(block, url); }, 1500);
    });
  }

  function startTimer(block) {
    if (!block || !block.dataset || block.dataset.remainingMs === undefined) return;
    var deadline = Date.now() + Number(block.dataset.remainingMs);
    var count = block.querySelector("[data-timer-count]");
    var close = block.querySelector("[data-timer-close]");
    function tick() {
      if (!block.isConnected) return;
      var left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      if (count) {
        count.textContent = interpolate(gettext("%s s"), [left]);
        count.classList.toggle("urgent", left <= 5);
      }
      if (left <= 0) {
        block.querySelectorAll("input, textarea").forEach(function (el) { el.readOnly = true; });
        if (close) timerAction(block, close.getAttribute("formaction"));
        return;
      }
      setTimeout(tick, 250);
    }
    tick();
  }

  form.querySelectorAll("[data-timed][data-remaining-ms]").forEach(startTimer);

  form.addEventListener("click", function (e) {
    var button = e.target.closest("[data-timer-open], [data-timer-close]");
    if (!button) return;
    e.preventDefault();
    timerAction(button.closest("[data-timed]"), button.getAttribute("formaction"));
  });

  // Enviar o guardar con el botón: viajan también las señales pendientes.
  form.addEventListener("submit", function () {
    clearTimeout(timer);
    telemetryField.value = JSON.stringify(delta);
  });
})();
