// Clase en vivo: un flujo SSE avisa de los cambios; la página pide al servidor el fragmento
// HTML que le toca y lo reemplaza. Toda la lógica de estado vive en el servidor.
(function () {
  "use strict";

  function csrf() {
    var el = document.querySelector("input[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  // Aviso no bloqueante: un alert() congelaría el proyector frente a la clase.
  function toast(message) {
    var el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = "show";
    clearTimeout(toast.handle);
    toast.handle = setTimeout(function () { el.className = ""; }, 3000);
  }

  function setConn(state, text) {
    var el = document.getElementById("conn");
    if (!el) return;
    el.className = "conn " + state;
    el.textContent = text;
  }

  // Cuenta regresiva basada en la hora del servidor: el reloj del teléfono puede estar mal.
  var timerHandle = null;
  function startTimers(stage, onZero) {
    if (timerHandle) clearInterval(timerHandle);
    var el = stage.querySelector("[data-closes-at]");
    if (!el || !el.dataset.closesAt) return;
    var closes = Date.parse(el.dataset.closesAt);
    var offset = Date.parse(el.dataset.serverNow) - Date.now();
    var fired = false;
    function tick() {
      var left = Math.max(0, Math.ceil((closes - (Date.now() + offset)) / 1000));
      el.textContent = left + " s";
      el.classList.toggle("low", left <= 5);
      if (left === 0 && !fired) {
        fired = true;
        clearInterval(timerHandle);
        // El servidor cierra la pregunta al vencer; el SSE lo avisará, pero se pide ya
        // para no depender de la latencia del sondeo.
        setTimeout(onZero, 400);
      }
    }
    tick();
    timerHandle = setInterval(tick, 250);
  }

  // Si la pregunta sigue siendo la misma (p. ej. el docente dio +15 s), lo que el estudiante
  // ya había marcado o escrito sobrevive al recargar el fragmento.
  function keepForm(stage) {
    var form = stage.querySelector("[data-answer-form]");
    if (!form) return null;
    var values = [];
    form.querySelectorAll("input, textarea").forEach(function (el) {
      if (el.type === "hidden") return;
      if (el.type === "radio" || el.type === "checkbox") { if (el.checked) values.push([el.name, el.value, true]); }
      else if (el.value) values.push([el.name, el.value, false]);
    });
    return { slide: form.dataset.slide, values: values };
  }

  function restoreForm(stage, kept) {
    if (!kept || !kept.values.length) return;
    var form = stage.querySelector("[data-answer-form]");
    if (!form || form.dataset.slide !== kept.slide) return;
    kept.values.forEach(function (v) {
      form.querySelectorAll("[name='" + v[0] + "']").forEach(function (el) {
        if (v[2]) { if (el.value === v[1]) el.checked = true; }
        else el.value = v[1];
      });
    });
  }

  function escapeHtml(t) {
    return t.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }

  function tex(body) {
    try { return window.katex.renderToString(body, { throwOnError: true }); }
    catch (err) { return '<span class="lp-error">' + escapeHtml(body) + " ⚠</span>"; }
  }

  /** HTML con el LaTeX de `text` renderizado, o "" si no lleva matemáticas. */
  function renderMath(text) {
    if (text.indexOf("$") >= 0) {
      var html = "", last = 0;
      text.replace(/\$\$[\s\S]+?\$\$|\$[^$]+\$/g, function (m, offset) {
        html += escapeHtml(text.slice(last, offset));
        html += tex(m.replace(/^\$+|\$+$/g, ""));
        last = offset + m.length;
      });
      return last ? html + escapeHtml(text.slice(last)) : "";
    }
    return /[\\^_{}]/.test(text) ? tex(text) : "";
  }

  function Live(opts) {
    var stage = document.getElementById("stage");
    var lastKey = null, carried = null;
    var loading = false, again = false;

    function refresh() {
      if (loading) { again = true; return; }
      loading = true;
      fetch(opts.fragment, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) {
          if (r.status === 404 || r.status === 403) throw new Error(gettext("Sin acceso a esta clase."));
          return r.text();
        })
        .then(function (html) { swap(html); })
        .catch(function (e) { setConn("off", e.message || gettext("sin conexión")); })
        .finally(function () {
          loading = false;
          if (again) { again = false; refresh(); }
        });
    }

    function swap(html) {
      // Lo marcado se arrastra también a través de pantallas sin formulario (p. ej. la pausa).
      var kept = keepForm(stage) || carried;
      stage.innerHTML = html;
      restoreForm(stage, kept);
      carried = stage.querySelector("[data-answer-form]") ? null : kept;
      if (window.policlaseMath) window.policlaseMath(stage);
      startTimers(stage, refresh);
      if (opts.afterSwap) opts.afterSwap(stage, refresh);
    }

    function connect() {
      var source = new EventSource(opts.events);
      source.onopen = function () { setConn("on", gettext("en vivo")); };
      source.onerror = function () { setConn("off", gettext("reconectando…")); };
      source.onmessage = function (event) {
        var data = JSON.parse(event.data);
        var key = opts.keyOf(data);
        if (key !== lastKey) { lastKey = key; refresh(); }
        if (data.status === "ended") { source.close(); setConn("", gettext("clase terminada")); }
      };
    }

    refresh();
    connect();
    return { refresh: refresh, swap: swap, stage: stage };
  }

  window.PoliclaseLive = {
    teacher: function (opts) {
      var live = Live({
        events: opts.events,
        fragment: opts.fragment,
        // El docente también quiere ver llegar las respuestas.
        keyOf: function (d) { return d.state_version + ":" + d.answers_version + ":" + d.status; },
      });

      function act(action, button) {
        if (button && button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
        var body = new URLSearchParams({ action: action });
        if (button && button.dataset.index !== undefined) body.set("index", button.dataset.index);
        fetch(opts.action, {
          method: "POST", credentials: "same-origin", body: body,
          headers: { "X-CSRFToken": csrf() },
        }).then(function (r) {
          if (r.status === 409) return r.text().then(toast);
          if (!r.ok) toast(interpolate(gettext("No se pudo aplicar la acción (%s)."), [r.status]));
        }).finally(live.refresh);
      }

      live.stage.addEventListener("click", function (e) {
        var button = e.target.closest("[data-action]");
        if (button && !button.disabled) act(button.dataset.action, button);
      });

      // Casillas de la sala de espera (p. ej. bono por rapidez): marcada = <nombre>_on.
      live.stage.addEventListener("change", function (e) {
        var box = e.target.closest("input[data-toggle-action]");
        if (box) act(box.dataset.toggleAction + (box.checked ? "_on" : "_off"));
      });

      // Posar el mouse sobre una miniatura muestra la diapositiva (sin marcar la respuesta:
      // el proyector lo ve la clase).
      if (window.policlaseHoverPreview) {
        window.policlaseHoverPreview(live.stage, ".live-outline .thumb", function (thumb) {
          var t = thumb.querySelector("template.thumb-preview");
          return t ? t.innerHTML : "";
        });
      }

      // Panel de diapositivas: se muestra u oculta y el navegador lo recuerda.
      var shell = document.getElementById("shell"), toggle = document.getElementById("toggle-outline");
      function setOutline(on) {
        shell.classList.toggle("show-outline", on);
        toggle.setAttribute("aria-pressed", on ? "true" : "false");
        try { localStorage.setItem("policlase.outline", on ? "1" : "0"); } catch (e) {}
      }
      if (shell && toggle) {
        try { if (localStorage.getItem("policlase.outline") === "0") setOutline(false); } catch (e) {}
        toggle.addEventListener("click", function () { setOutline(!shell.classList.contains("show-outline")); });
      }

      // Presentador inalámbrico (puntero láser): envía las mismas teclas que a PowerPoint.
      // «Adelante» hace el paso que toca —abrir la pregunta, cerrarla, mostrar la respuesta,
      // pasar a la siguiente—, así que el control basta para toda la clase.
      var FORWARD = ["PageDown", "ArrowRight", "ArrowDown", " ", "Enter"];
      var BACK = ["PageUp", "ArrowLeft", "ArrowUp"];
      document.addEventListener("keydown", function (e) {
        if (e.target.closest("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
        if (e.target.closest("button") && (e.key === " " || e.key === "Enter")) return;  // botón con foco
        if (FORWARD.indexOf(e.key) >= 0) { act("primary"); e.preventDefault(); }
        else if (BACK.indexOf(e.key) >= 0) { act("prev"); e.preventDefault(); }
        else if (e.key === "b" || e.key === "B" || e.key === ".") { toggleBlank(); e.preventDefault(); }
        else if (e.key === "F5") { toggleFullscreen(); e.preventDefault(); }
      });

      // Clic sobre la diapositiva (o el clic del puntero láser) también avanza, como en PowerPoint.
      live.stage.addEventListener("click", function (e) {
        if (e.target.closest("button, a, input, label, .live-outline, .controls, summary")) return;
        if (!e.target.closest(".projector")) return;
        act("primary");
      });

      // Pantalla en negro (tecla B o punto) y pantalla completa (F5 o su botón).
      var blank = null;
      function toggleBlank() {
        if (!blank) {
          blank = document.createElement("div");
          blank.className = "blank-screen";
          blank.addEventListener("click", toggleBlank);
          document.body.appendChild(blank);
        } else {
          blank.remove();
          blank = null;
        }
      }
      function toggleFullscreen() {
        if (document.fullscreenElement) document.exitFullscreen();
        else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
      }
      var full = document.getElementById("toggle-fullscreen");
      if (full) full.addEventListener("click", toggleFullscreen);
    },

    student: function (opts) {
      var live = Live({
        events: opts.events,
        fragment: opts.fragment,
        // Al estudiante solo le importa el estado: no recarga con cada respuesta ajena,
        // así no pierde lo que está marcando.
        keyOf: function (d) { return d.state_version + ":" + d.status; },
        afterSwap: bindForm,
      });

      // Respuestas de texto: si llevan LaTeX ($…$, \comandos, ^, _), se ven renderizadas al escribir.
      live.stage.addEventListener("input", function (e) {
        var input = e.target.closest("[data-math-preview]");
        if (!input || !window.katex) return;
        var box = input.parentNode.querySelector(".math-preview");
        if (!box) return;
        var text = input.value;
        var html = renderMath(text);
        box.hidden = !html;
        box.querySelector(".math-out").innerHTML = html || "";
      });

      function bindForm(stage) {
        var form = stage.querySelector("[data-answer-form]");
        if (!form) return;
        form.addEventListener("submit", function (e) {
          e.preventDefault();
          var button = form.querySelector("button[type=submit]");
          if (button) { button.disabled = true; button.textContent = gettext("Enviando…"); }
          fetch(form.action, { method: "POST", credentials: "same-origin", body: new FormData(form) })
            .then(function (r) { return r.text(); })
            .then(function (html) { live.swap(html); })
            .catch(function () {
              if (button) { button.disabled = false; button.textContent = gettext("Enviar respuesta"); }
              toast(gettext("No se pudo enviar. Revise su conexión e intente de nuevo."));
            });
        });
      }
    },
  };
})();
