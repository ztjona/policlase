// Intento de una actividad: autoguardado, imágenes que se suben al elegirlas, señales para el
// docente (pegar texto, salir de la página), los paneles de índice y pendientes, y seguir al docente
// cuando presenta la actividad. Sin JavaScript, el formulario se guarda y se envía con sus botones,
// imágenes incluidas.
(function () {
  "use strict";
  var form = document.getElementById("attempt-form");
  if (!form) return;
  var status = form.querySelector("[data-save-status]");
  var telemetryField = form.querySelector("input[name=telemetry]");
  var delta = { pastes: 0, pasted_chars: 0, blurs: 0, blocked_pastes: 0, away_s: 0, away_max_s: 0, q_s: {} };
  var timer = null, dirty = false, saving = false, away = false, awaySince = 0, failures = 0;
  // No `form.action`: los botones se llaman «action», y el navegador devuelve esos botones en vez de
  // la dirección del formulario (el autoguardado iba a «…/intento/[object RadioNodeList]»).
  var saveUrl = form.getAttribute("action") || location.pathname;

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
  // Salir de la página: cambiar de pestaña o de ventana. Se cuenta una vez por salida y, al volver,
  // se suma cuánto duró; la salida más larga se envía aparte. Si no vuelve, esa duración no se registra.
  function leave() { if (!away) { away = true; awaySince = Date.now(); delta.blurs += 1; flush(document.hidden); } }
  function back() {
    if (!away) return;
    away = false;
    var seconds = Math.round((Date.now() - awaySince) / 1000);
    delta.away_s += seconds;
    delta.away_max_s = Math.max(delta.away_max_s, seconds);
  }
  document.addEventListener("visibilitychange", function () { if (document.hidden) leave(); else back(); });
  window.addEventListener("blur", leave);
  window.addEventListener("focus", back);

  // Tiempo en cada pregunta: cada segundo con la página visible se suma a la pregunta en curso, la que
  // tiene el foco o, si ninguna lo tiene, la que cruza el centro de la pantalla (la más interna, si
  // una pregunta de seguimiento está dentro de otra). Pasar a otra ventana, como la calculadora, sigue
  // contando; cambiar de pestaña, no.
  var blocks = Array.prototype.slice.call(form.querySelectorAll(".q-block[id^='q-']"));
  function currentBlock() {
    var focused = document.activeElement && document.activeElement.closest ? document.activeElement.closest(".q-block[id^='q-']") : null;
    if (focused && form.contains(focused)) return focused;
    var middle = window.innerHeight / 2, best = null, bestDistance = Infinity;
    blocks.forEach(function (b) {
      var r = b.getBoundingClientRect();
      if (!r.height) return;                     // oculta: sección deshabilitada o panel plegado
      var d = r.top <= middle && r.bottom >= middle ? 0 : Math.min(Math.abs(r.top - middle), Math.abs(r.bottom - middle));
      if (d < bestDistance || (d === bestDistance && best && best.contains(b))) { best = b; bestDistance = d; }
    });
    return best;
  }
  setInterval(function () {
    if (document.hidden) return;
    var b = currentBlock();
    if (!b) return;
    var key = b.id.slice(2);
    delta.q_s[key] = (delta.q_s[key] || 0) + 1;
  }, 1000);

  // ------------------------------------------------------------------ autoguardado

  function payload() {
    telemetryField.value = JSON.stringify(delta);
    var data = new FormData(form);
    Array.from(data.keys()).forEach(function (k) { if (k.indexOf("f-") === 0) data.delete(k); });
    data.set("action", "save");
    return data;
  }

  function resetDelta(sent) {
    Object.keys(sent.q_s || {}).forEach(function (q) {
      delta.q_s[q] = (delta.q_s[q] || 0) - sent.q_s[q];
      if (delta.q_s[q] <= 0) delete delta.q_s[q];
    });
    Object.keys(delta).forEach(function (k) {
      if (k === "q_s") return;
      // La salida más larga es un máximo, no una suma: lo enviado ya quedó registrado.
      if (k === "away_max_s") delta[k] = delta[k] > (sent[k] || 0) ? delta[k] : 0;
      else delta[k] = Math.max(0, delta[k] - (sent[k] || 0));
    });
  }

  // Qué decirle al estudiante cuando un guardado falla: sin conexión no es lo mismo que una sesión
  // cerrada o un borrador que ya no existe, y en esos casos reintentar no sirve.
  function failure(code, message) {
    if (message) return message;
    if (code === 403) return gettext("Su sesión se cerró y los últimos cambios no se guardaron. Copie sus respuestas recientes, recargue la página e ingrese de nuevo.");
    if (code === 404) return gettext("Este borrador ya no está abierto; es posible que se haya enviado desde otra pestaña. Recargue la página.");
    if (code) return gettext("El servidor no pudo guardar los cambios. Puede seguir escribiendo; se volverá a intentar automáticamente.");
    return gettext("Sin conexión: no se guardó. Puede seguir escribiendo; se volverá a intentar automáticamente.");
  }

  function save(keepalive) {
    if (saving) { dirty = true; return; }
    saving = true;
    dirty = false;
    var sent = JSON.parse(JSON.stringify(delta)), code = 0, message = "";
    say(gettext("Guardando…"));
    return fetch(saveUrl, {
      method: "POST", credentials: "same-origin", body: payload(), keepalive: !!keepalive,
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
    }).then(function (r) {
      if (r.ok) return r.json();
      code = r.status;
      // 409: el intento ya no está abierto (se envió, también automáticamente al vencer).
      return r.json().catch(function () { return {}; }).then(function (res) {
        message = res.error || "";
        throw new Error(code);
      });
    }).then(function (data) {
      failures = 0;
      resetDelta(sent);
      say(interpolate(gettext("Guardado automáticamente a las %s"), [data.saved_at]));
    }).catch(function () {
      failures += 1;
      if (code !== 403 && code !== 404 && code !== 409) dirty = true;
      say(failure(code, message));
    }).then(function () {
      saving = false;
      if (dirty) schedule();
    });
  }

  // Tras un fallo, se espera cada vez más (hasta 30 s) para no insistir sin pausa.
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(save, failures ? Math.min(30000, 2500 * Math.pow(2, failures - 1)) : 2500);
  }
  // `keepalive` solo al ocultarse la página: deja terminar el guardado si se cierra, pero limita el
  // cuerpo a 64 KB.
  function flush(keepalive) { clearTimeout(timer); save(keepalive); }

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
  setInterval(function () {
    if (delta.pastes || delta.blurs || delta.blocked_pastes || delta.away_s || Object.keys(delta.q_s).length) save();
  }, 60000);

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
        if (window.policlaseAttemptRefresh) window.policlaseAttemptRefresh();
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
        if (window.policlaseAttemptRefresh) window.policlaseAttemptRefresh();
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
    if (window.policlaseAttemptRefresh) window.policlaseAttemptRefresh();
    return fresh;
  }

  function timerAction(block, url) {
    if (block.dataset.busy) return;
    block.dataset.busy = "1";
    var sent = JSON.parse(JSON.stringify(delta));
    var data = payload();
    return fetch(url, {
      method: "POST", credentials: "same-origin", body: data,
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
    }).then(function (r) { return r.json(); }).then(function (res) {
      // El servidor ya sumó estas señales: no se vuelven a enviar en el próximo guardado.
      if (res.html) { resetDelta(sent); replaceTimed(block, res.html); }
      else { delete block.dataset.busy; if (res.error) say(res.error); }
    }).catch(function () {
      delete block.dataset.busy;
      say(gettext("Sin conexión: no se pudo enviar. Se volverá a intentar automáticamente."));
      setTimeout(function () { timerAction(block, url); }, 1500);
    });
  }

  // Sin cuenta regresiva a la vista: 10 s antes del final, el marco cambia (una señal discreta) y,
  // al acabarse, aparece el aviso de que todavía puede responder con penalización. La pregunta
  // sigue abierta hasta que el estudiante pulsa «Responder» o envía el intento.
  function startTimer(block) {
    if (!block || !block.dataset || block.dataset.remainingMs === undefined) return;
    var end = Date.now() + Number(block.dataset.remainingMs);
    function tick() {
      if (!block.isConnected) return;
      var left = end - Date.now();
      block.classList.toggle("ending", left > 0 && left <= 10000);
      if (left > 0) setTimeout(tick, 500);
    }
    tick();
  }

  form.querySelectorAll("[data-timed][data-remaining-ms]").forEach(startTimer);

  form.addEventListener("click", function (e) {
    var button = e.target.closest("[data-timer-open], [data-timer-close]");
    if (!button) return;
    e.preventDefault();
    var block = button.closest("[data-timed]");
    if (button.hasAttribute("data-timer-close") && !answered(block) &&
        !window.confirm(gettext("Todavía no ha respondido esta pregunta. Si la cierra ahora, ya no podrá responderla. ¿Cerrarla?"))) return;
    timerAction(block, button.getAttribute("formaction"));
  });


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
        headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
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

  // ------------------------------------------------------------------ repaso
  // «Comprobar»: viaja el formulario entero; el servidor califica esa pregunta y devuelve su
  // corrección y, si ya se comprobó todo el ejercicio, su explicación.
  function checkAction(block, url) {
    if (block.dataset.busy) return;
    block.dataset.busy = "1";
    var sent = JSON.parse(JSON.stringify(delta));
    fetch(url, {
      method: "POST", credentials: "same-origin", body: payload(),
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (!res.html) { delete block.dataset.busy; if (res.error) say(res.error); return; }
      resetDelta(sent);
      var holder = document.createElement("div");
      holder.innerHTML = res.html;
      var fresh = holder.firstElementChild, section = block.closest("[data-item]");
      block.replaceWith(fresh);
      if (window.policlaseMath) window.policlaseMath(fresh);
      if (res.explanation && section && !section.querySelector(".explain")) {
        holder.innerHTML = res.explanation;
        var explain = holder.firstElementChild;
        section.appendChild(explain);
        if (window.policlaseMath) window.policlaseMath(explain);
      }
      if (window.policlaseAttemptRefresh) window.policlaseAttemptRefresh();
    }).catch(function () {
      delete block.dataset.busy;
      say(gettext("Sin conexión: no se pudo comprobar. Inténtelo de nuevo."));
    });
  }

  form.addEventListener("click", function (e) {
    var button = e.target.closest("[data-check]");
    if (!button) return;
    e.preventDefault();
    checkAction(button.closest("[data-q]"), button.getAttribute("formaction"));
  });

  // Enter en un campo de texto no envía el formulario: el autoguardado ya guarda, y el envío
  // implícito pulsaría el primer botón de la página (por ejemplo, «Responder» de otra pregunta).
  form.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.target.tagName !== "INPUT") return;
    if (["submit", "button", "checkbox", "radio", "file"].indexOf(e.target.type) === -1) e.preventDefault();
  });

  // Enviar o guardar con el botón: viajan también las señales pendientes.
  form.addEventListener("submit", function () {
    clearTimeout(timer);
    telemetryField.value = JSON.stringify(delta);
  });

  // Con el autoguardado, «Guardar borrador» sobra: queda para cuando no hay JavaScript. Ctrl+S
  // (o Cmd+S) guarda al instante, como en un editor.
  var saveButton = form.querySelector("[data-save-button]");
  if (saveButton) saveButton.hidden = true;
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) { e.preventDefault(); flush(false); }
  });

  // ------------------------------------------------------------------ estado de las preguntas e índice
  // Lo mismo que el servidor considera «en blanco» (engine.blank): sin texto, sin marca, sin imagen.

  var submitButton = form.querySelector("[data-submit]");
  var baseConfirm = submitButton ? submitButton.dataset.confirm : "";

  function answered(block) {
    if (block.dataset.done !== undefined) return block.dataset.done === "1";        // con tiempo, cerrada
    if (block.classList.contains("timed-locked")) return false;
    if (block.querySelector(".upload")) return true;
    return Array.from(block.querySelectorAll("input, textarea")).some(function (el) {
      if (el.name === "mark" || el.type === "file" || el.type === "hidden" || el.name.indexOf("w-") === 0) return false;
      if (el.type === "radio" || el.type === "checkbox") return el.checked;
      return el.value.trim() !== "";
    });
  }

  function setText(selector, value) {
    document.querySelectorAll(selector).forEach(function (el) { if (el.textContent !== String(value)) el.textContent = value; });
  }

  function refresh() {
    var pending = 0, done = 0, marked = 0, perItem = {};
    form.querySelectorAll("[data-q]").forEach(function (block) {
      var key = block.dataset.q, isDone = answered(block);
      var mark = block.querySelector("input[name=mark]"), isMarked = !!(mark && mark.checked);
      if (isDone) done += 1; else pending += 1;
      if (isMarked) marked += 1;
      var chip = document.querySelector('[data-qchip="' + CSS.escape(key) + '"]');
      if (chip) {
        chip.classList.toggle("done", isDone);
        chip.classList.toggle("marked", isMarked);
      }
      var item = block.closest("[data-item]");
      if (item) {
        var c = perItem[item.dataset.item] || (perItem[item.dataset.item] = { done: 0, total: 0 });
        c.total += 1;
        if (isDone) c.done += 1;
      }
    });
    setText("[data-pending-count]", pending);
    setText("[data-answered-count]", done);
    setText("[data-marked-count]", marked);
    Object.keys(perItem).forEach(function (id) {
      var el = document.querySelector('[data-toc-count="' + CSS.escape(id) + '"]');
      if (!el) return;
      el.textContent = perItem[id].done + "/" + perItem[id].total;
      el.classList.toggle("done", perItem[id].done === perItem[id].total);
    });
    // Enviar con preguntas en blanco es posible (valen cero), pero se avisa cuántas son.
    if (submitButton) {
      var parts = [];
      if (pending) parts.push(interpolate(ngettext("Queda %s pregunta sin responder, que vale cero.",
                                                   "Quedan %s preguntas sin responder, que valen cero.", pending), [pending]));
      if (marked) parts.push(interpolate(ngettext("%s pregunta está marcada para revisar.",
                                                  "%s preguntas están marcadas para revisar.", marked), [marked]));
      parts.push(baseConfirm);
      submitButton.dataset.confirm = parts.join(" ");
    }
  }
  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);
  window.policlaseAttemptRefresh = refresh;     // tras subir o quitar una imagen, o abrir una pregunta con tiempo
  refresh();

  // Paneles laterales. Los botones solo sirven con JavaScript: llegan ocultos y se muestran aquí.
  document.querySelectorAll("[data-side-open], [data-side-collapse], [data-side-expand]").forEach(function (b) { b.hidden = false; });

  // En pantalla ancha, cada panel se pliega a un costado; este navegador lo recuerda.
  function setCollapsed(side, collapsed) {
    side.classList.toggle("collapsed", collapsed);
    try { localStorage.setItem("policlase.side." + side.id, collapsed ? "1" : "0"); } catch (e) {}
  }
  document.querySelectorAll(".attempt-side").forEach(function (side) {
    try { if (localStorage.getItem("policlase.side." + side.id) === "1") side.classList.add("collapsed"); } catch (e) {}
    side.querySelector("[data-side-collapse]").addEventListener("click", function () { setCollapsed(side, true); });
    side.querySelector("[data-side-expand]").addEventListener("click", function () { setCollapsed(side, false); });
  });

  // En pantalla angosta se abren sobre la página con los botones de abajo.
  function closeSides() { document.querySelectorAll(".attempt-side.open").forEach(function (a) { a.classList.remove("open"); }); }
  document.addEventListener("click", function (e) {
    var open = e.target.closest("[data-side-open]");
    if (open) {
      var side = document.getElementById(open.dataset.sideOpen);
      var wasOpen = side.classList.contains("open");
      closeSides();
      if (!wasOpen) side.classList.add("open");
      return;
    }
    if (e.target.closest("[data-side-close]") || e.target.closest(".attempt-side.open a")) { closeSides(); return; }
    if (!e.target.closest(".attempt-side")) closeSides();          // tocar fuera del panel lo cierra
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSides(); });

  // El índice resalta la parte que se está leyendo.
  var tocLinks = Array.from(document.querySelectorAll("[data-toc]"));
  var blocks = Array.from(form.querySelectorAll("[data-block]"));
  var ticking = false;
  function highlight() {
    ticking = false;
    var here = null;
    blocks.forEach(function (b) { if (b.getBoundingClientRect().top < 140) here = b; });
    var at = here ? Number(here.dataset.block) : -1, best = null;
    tocLinks.forEach(function (a) { if (Number(a.dataset.toc) <= at) best = a; });
    tocLinks.forEach(function (a) { if (a === best) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); });
  }
  window.addEventListener("scroll", function () { if (!ticking) { ticking = true; requestAnimationFrame(highlight); } }, { passive: true });
  highlight();

  // ------------------------------------------------------------------ tiempo de una evaluación
  // El límite lo pone el servidor (engine.check_time); aquí se muestra la cuenta regresiva y, al
  // llegar a cero, se envía la evaluación con lo que haya (el servidor la cierra igual si la página
  // no lo hace).

  if (form.dataset.remainingMs !== undefined) {
    var end = Date.now() + Number(form.dataset.remainingMs);
    var clock = form.querySelector("[data-clock]"), value = form.querySelector("[data-clock-value]");
    var toggleClock = form.querySelector("[data-clock-toggle]");
    var reminder = document.querySelector("[data-clock-reminder]"), reminded = {};
    var pad = function (n) { return String(n).padStart(2, "0"); };
    var finished = false;
    // Oculto por omisión (sin presión a la vista); este navegador recuerda si se prefiere verlo.
    var showClock = function (on) {
      clock.hidden = !on;
      toggleClock.textContent = on ? toggleClock.dataset.hide : toggleClock.dataset.show;
      toggleClock.setAttribute("aria-expanded", on ? "true" : "false");
      try { sessionStorage.setItem("policlase.clock", on ? "1" : "0"); } catch (e) {}
    };
    toggleClock.addEventListener("click", function () { showClock(clock.hidden); });
    try { if (sessionStorage.getItem("policlase.clock") === "1") showClock(true); } catch (e) {}
    var remind = function (left) {
      [[600, gettext("Quedan 10 minutos de evaluación.")], [120, gettext("Quedan 2 minutos de evaluación.")]].forEach(function (r) {
        if (left <= r[0] && left > r[0] - 60 && !reminded[r[0]]) {
          reminded[r[0]] = true;
          reminder.textContent = r[1];
          reminder.hidden = false;
          setTimeout(function () { reminder.hidden = true; }, 12000);
        }
      });
    };
    var tickClock = function () {
      var left = Math.max(0, Math.ceil((end - Date.now()) / 1000));
      var h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), sec = left % 60;
      value.textContent = (h ? h + ":" + pad(m) : m) + ":" + pad(sec);
      clock.classList.toggle("low", left <= 300);
      clock.classList.toggle("urgent", left <= 60);
      remind(left);
      if (left <= 0 && !finished) {
        finished = true;
        say(gettext("Se acabó el tiempo. Se está enviando la evaluación…"));
        var action = document.createElement("input");
        action.type = "hidden"; action.name = "action"; action.value = "timeout";
        form.appendChild(action);
        HTMLFormElement.prototype.submit.call(form);     // sin pasar por la confirmación de «Enviar»
        return;
      }
      setTimeout(tickClock, 500);
    };
    tickClock();
  }

  // ------------------------------------------------------------------ seguir al docente
  // Si el docente presenta la actividad, se ofrece ir a la diapositiva donde está, volver a donde se
  // estaba o seguirlo automáticamente. Se consulta cada 5 s mientras presenta y cada 20 s si no.

  var bar = document.querySelector("[data-pointer]");
  var pointerUrl = form.dataset.pointerUrl;
  if (bar && pointerUrl) {
    var titleBox = bar.querySelector("[data-pointer-title]");
    var go = bar.querySelector("[data-pointer-go]"), backButton = bar.querySelector("[data-pointer-back]");
    var follow = bar.querySelector("[data-pointer-follow]");
    var last = null, saved = null, poll = null;
    try { follow.checked = sessionStorage.getItem("policlase.follow") === "1"; } catch (e) {}

    // El intento puede ser de una versión anterior de la actividad: se busca primero el ítem o el
    // título de la diapositiva del docente, y solo si no está, la misma posición.
    var plain = function (t) { return (t || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ""); };
    var byHeading = function (text) {
      var wanted = plain(text);
      return wanted ? tocLinks.find(function (a) { return plain(a.textContent) === wanted; }) : null;
    };
    var target = function (state) {
      if (state.item) {
        var byItem = form.querySelector('[data-item="' + CSS.escape(state.item) + '"]');
        if (byItem) return byItem;
      }
      var link = byHeading(state.heading);
      if (link) return form.querySelector('[data-block="' + link.dataset.toc + '"]');
      var best = null;                                   // el bloque más cercano antes de esa posición
      blocks.forEach(function (b) { if (Number(b.dataset.block) <= state.at) best = b; });
      return best;
    };
    var titleOf = function (state) {
      if (state.heading) return state.heading;
      var el = target(state), best = null;
      var at = el ? Number(el.dataset.block) : state.at;
      tocLinks.forEach(function (a) { if (Number(a.dataset.toc) <= at) best = a; });
      return best ? best.textContent.trim() : gettext("el comienzo de la actividad");
    };
    var jump = function (state) {
      var el = target(state);
      if (!el) return;
      if (saved === null) saved = window.scrollY;
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      backButton.hidden = false;
    };
    var check = function () {
      if (document.hidden) { schedulePoll(20000); return; }
      fetch(pointerUrl, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.json() : { presenting: false }; })
        .then(function (state) {
          bar.hidden = !state.presenting;
          if (!state.presenting) { last = null; schedulePoll(20000); return; }
          titleBox.textContent = titleOf(state);
          var changed = !last || last.at !== state.at;
          last = state;
          if (changed && follow.checked) jump(state);
          schedulePoll(5000);
        })
        .catch(function () { schedulePoll(20000); });
    };
    var schedulePoll = function (ms) { clearTimeout(poll); poll = setTimeout(check, ms); };
    go.addEventListener("click", function () { if (last) jump(last); });
    backButton.addEventListener("click", function () {
      if (saved !== null) window.scrollTo({ top: saved, behavior: "smooth" });
      saved = null;
      backButton.hidden = true;
      if (follow.checked) { follow.checked = false; follow.dispatchEvent(new Event("change")); }
    });
    follow.addEventListener("change", function () {
      try { sessionStorage.setItem("policlase.follow", follow.checked ? "1" : "0"); } catch (e) {}
      if (follow.checked && last) jump(last);
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) schedulePoll(300); });
    check();
  }
})();
