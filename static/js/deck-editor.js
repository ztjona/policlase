// Editor de presentaciones: panel de diapositivas | YAML en Monaco (el editor de VS Code) | vista previa.
//
// El servidor valida y renderiza (deck_preview) mientras se escribe; los errores aparecen
// subrayados en su línea. El panel lateral reordena y oculta diapositivas editando el texto
// (deck_ops), así que todo se deshace con Ctrl+Z y nada se guarda hasta «Guardar».
// Si Monaco no carga, queda el área de texto de siempre.
(function () {
  "use strict";
  var form = document.getElementById("deck-form");
  if (!form) return;
  var textarea = document.getElementById("source");
  var file = document.getElementById("file");
  var preview = document.getElementById("preview");
  var outline = document.getElementById("outline");
  var status = document.getElementById("preview-status");
  var diag = document.getElementById("diagnostics");
  var titleEl = document.getElementById("deck-title");
  var body = document.getElementById("studio-body");

  var lines = [];            // línea (1-based) donde empieza cada diapositiva
  var timer = null, seq = 0, monaco = null, editor = null;

  function csrf() { return form.querySelector("input[name=csrfmiddlewaretoken]").value; }

  // ------------------------------------------------------------ editor (Monaco o textarea)

  var ed = {
    get: function () { return editor ? editor.getValue() : textarea.value; },
    set: function (text) {
      if (editor) {
        // Como una edición más: queda en el historial de deshacer.
        editor.pushUndoStop();
        editor.executeEdits("policlase", [{ range: editor.getModel().getFullModelRange(), text: text }]);
        editor.pushUndoStop();
      } else {
        textarea.value = text;
      }
    },
    line: function () {
      if (editor) return editor.getPosition().lineNumber;
      return textarea.value.slice(0, textarea.selectionStart).split("\n").length;
    },
    goTo: function (line) {
      if (editor) {
        editor.revealLineInCenter(line);
        editor.setPosition({ lineNumber: line, column: 1 });
        editor.focus();
        return;
      }
      var parts = textarea.value.split("\n"), pos = 0;
      for (var i = 0; i < line - 1 && i < parts.length; i++) pos += parts[i].length + 1;
      textarea.focus();
      textarea.setSelectionRange(pos, pos + (parts[line - 1] || "").length);
      var lh = parseFloat(getComputedStyle(textarea).lineHeight) || 19;
      textarea.scrollTop = Math.max(0, (line - 5) * lh);
    },
    select: function (fromLine, toLine) {
      if (editor) {
        editor.setSelection({ startLineNumber: fromLine, startColumn: 1, endLineNumber: toLine, endColumn: 1 });
        editor.revealLineInCenter(fromLine);
        editor.focus();
      }
    },
    markers: function (list) {
      if (!editor) return;
      var model = editor.getModel();
      monaco.editor.setModelMarkers(model, "policlase", list.filter(function (d) { return d.line; }).map(function (d) {
        var line = Math.min(d.line, model.getLineCount());
        return {
          severity: d.severity === "error" ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
          message: d.code + " " + d.message, startLineNumber: line, endLineNumber: line,
          startColumn: model.getLineFirstNonWhitespaceColumn(line) || 1, endColumn: model.getLineMaxColumn(line),
        };
      }));
    },
  };

  function darkTheme() {
    var t = document.documentElement.dataset.theme;
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function startMonaco() {
    if (window.innerWidth < 720) return start();       // en el teléfono, el área de texto
    var timeout = setTimeout(start, 8000);             // red lenta o bloqueada: área de texto
    // Configuración del cargador AMD (la lee al cargarse); menús en español si la página lo está.
    window.require = { paths: { vs: form.dataset.monaco + "vs" } };
    if (form.dataset.lang === "es") window.require["vs/nls"] = { availableLanguages: { "*": "es" } };
    var loader = document.createElement("script");
    loader.src = form.dataset.monaco + "vs/loader.js";
    loader.onload = function () { loadEditor(timeout); };
    loader.onerror = function () { clearTimeout(timeout); start(); };
    document.body.appendChild(loader);
  }

  function loadEditor(timeout) {
    window.MonacoEnvironment = {
      getWorkerUrl: function () {
        var base = location.origin + form.dataset.monaco;
        return URL.createObjectURL(new Blob([
          "self.MonacoEnvironment={baseUrl:'" + base + "'};importScripts('" + base + "vs/base/worker/workerMain.js');",
        ], { type: "text/javascript" }));
      },
    };
    window.require(["vs/editor/editor.main"], function () {
      clearTimeout(timeout);
      if (started) return;
      monaco = window.monaco;
      var host = document.getElementById("monaco");
      editor = monaco.editor.create(host, {
        value: textarea.value, language: "yaml", theme: darkTheme() ? "vs-dark" : "vs",
        automaticLayout: true, tabSize: 2, insertSpaces: true, detectIndentation: false,
        minimap: { enabled: false }, wordWrap: "on", fontSize: 13, scrollBeyondLastLine: false,
        renderWhitespace: "boundary", rulers: [], quickSuggestions: false,
      });
      form.classList.add("has-monaco");
      editor.onDidChangeModelContent(schedule);
      editor.onDidChangeCursorPosition(function () { highlight(true); });
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function () { form.requestSubmit(); });
      start();
    }, function () { clearTimeout(timeout); start(); });
  }

  // ------------------------------------------------------------------ vista previa

  function currentSlide() {
    var line = ed.line(), index = -1;
    lines.forEach(function (start, i) { if (start && start <= line) index = i; });
    return index;
  }

  function highlight(scroll) {
    var index = currentSlide();
    [preview, outline].forEach(function (root) {
      root.querySelectorAll("[data-index]").forEach(function (el) {
        var on = Number(el.dataset.index) === index;
        el.classList.toggle("current", on);
        if (on && scroll) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    });
  }

  function showDiagnostics(list) {
    diag.innerHTML = "";
    list.forEach(function (d) {
      var li = document.createElement("li");
      li.className = d.severity;
      var code = document.createElement("code");
      code.textContent = d.code;
      li.appendChild(code);
      li.appendChild(document.createTextNode(" " + d.message +
        (d.line ? " · " + interpolate(gettext("línea %s"), [d.line]) : "") + (d.where ? " · " + d.where : "")));
      if (d.line) { li.dataset.line = d.line; li.classList.add("jump"); }
      diag.appendChild(li);
    });
    ed.markers(list);
  }

  function refresh() {
    var mine = ++seq;
    status.textContent = gettext("validando…");
    fetch(form.dataset.preview, {
      method: "POST", credentials: "same-origin", body: new URLSearchParams({ source: ed.get() }),
      headers: { "X-CSRFToken": csrf() },
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (mine !== seq) return;                       // llegó una respuesta más nueva
      lines = data.lines || [];
      showDiagnostics(data.diagnostics || []);
      var errors = (data.diagnostics || []).filter(function (d) { return d.severity === "error"; }).length;
      if (data.html !== null && data.html !== undefined) {
        preview.innerHTML = data.html;
        outline.innerHTML = data.outline;
        if (window.policlaseMath) { window.policlaseMath(preview); window.policlaseMath(outline); }
        if (data.title) titleEl.textContent = data.title;
        status.textContent = gettext("al día");
        preview.classList.remove("stale");
        outline.classList.remove("stale");
      } else {
        // Con errores se conserva la última vista válida, atenuada.
        status.textContent = interpolate(ngettext("%s error", "%s errores", errors), [errors]);
        preview.classList.add("stale");
        outline.classList.add("stale");
      }
      highlight(false);
    }).catch(function () { status.textContent = gettext("sin conexión"); });
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 450); }

  // ------------------------------------------------------------------ panel lateral

  function op(name, index, target) {
    if (outline.classList.contains("stale")) {
      status.textContent = gettext("Corrija los errores antes de usar el panel.");
      return;
    }
    var params = { source: ed.get(), op: name, index: index };
    if (target !== undefined) params.target = target;
    fetch(form.dataset.ops, {
      method: "POST", credentials: "same-origin", body: new URLSearchParams(params),
      headers: { "X-CSRFToken": csrf() },
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.error) { status.textContent = data.error; return; }
      ed.set(data.source);
      refresh();
    });
  }

  outline.addEventListener("click", function (e) {
    var thumb = e.target.closest(".thumb");
    if (!thumb) return;
    var index = Number(thumb.dataset.index), button = e.target.closest("[data-op]");
    var count = outline.querySelectorAll(".thumb:not(.is-fixed)").length;
    if (button) {
      var name = button.dataset.op;
      if (thumb.dataset.kind === "feedback") op(name === "hide" ? "feedback_off" : "feedback_on", index);
      else if (name === "up" && index > 0) op("move", index, index - 1);
      else if (name === "down" && index < count - 1) op("move", index, index + 1);
      else if (name === "hide" || name === "show") op(name, index);
      return;
    }
    if (lines[index]) ed.goTo(lines[index]);
  });

  // Arrastrar y soltar para reordenar.
  var dragged = null;
  outline.addEventListener("dragstart", function (e) {
    var thumb = e.target.closest(".thumb[draggable=true]");
    if (!thumb) return;
    dragged = Number(thumb.dataset.index);
    thumb.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragged));
  });
  outline.addEventListener("dragend", function () {
    outline.querySelectorAll(".dragging, .drop-before, .drop-after").forEach(function (el) {
      el.classList.remove("dragging", "drop-before", "drop-after");
    });
  });
  outline.addEventListener("dragover", function (e) {
    var thumb = e.target.closest(".thumb:not(.is-fixed)");
    if (dragged === null || !thumb) return;
    e.preventDefault();
    outline.querySelectorAll(".drop-before, .drop-after").forEach(function (el) { el.classList.remove("drop-before", "drop-after"); });
    var after = e.offsetY > thumb.offsetHeight / 2;
    thumb.classList.add(after ? "drop-after" : "drop-before");
  });
  outline.addEventListener("drop", function (e) {
    var thumb = e.target.closest(".thumb:not(.is-fixed)");
    if (dragged === null || !thumb) return;
    e.preventDefault();
    var target = Number(thumb.dataset.index);
    if (thumb.classList.contains("drop-after")) target += 1;
    if (target > dragged) target -= 1;
    var from = dragged;
    dragged = null;
    if (target !== from) op("move", from, target);
  });

  // ------------------------------------------------------------------ plantillas

  function itemId() { return "L-" + Math.random().toString(36).slice(2, 7); }

  var SNIPPETS = {
    content: function () {
      return "  - markdown: |\n      ## " + gettext("Título") + "\n      " + gettext("Texto con matemáticas: $f(x) = x^2$.") + "\n";
    },
    choice: function () {
      return "  - item:\n      id: " + itemId() + "\n      points: 1\n      questions:\n        - id: q1\n          type: choice\n          points: 1\n" +
        "          prompt: \"" + gettext("¿Pregunta?") + "\"\n          options:\n" +
        "            - { text: \"" + gettext("Correcta") + "\", correct: true }\n" +
        "            - { text: \"" + gettext("Distractor") + " 1\" }\n            - { text: \"" + gettext("Distractor") + " 2\" }\n";
    },
    multi_choice: function () {
      return "  - item:\n      id: " + itemId() + "\n      points: 1\n      questions:\n        - id: q1\n          type: multi_choice\n          points: 1\n" +
        "          prompt: \"" + gettext("Marque todas las correctas") + "\"\n          options:\n" +
        "            - { text: \"" + gettext("Correcta") + " 1\", correct: true }\n            - { text: \"" + gettext("Correcta") + " 2\", correct: true }\n" +
        "            - { text: \"" + gettext("Distractor") + "\" }\n";
    },
    true_false: function () {
      return "  - item:\n      id: " + itemId() + "\n      points: 1\n      questions:\n        - id: q1\n          type: true_false\n          points: 1\n" +
        "          prompt: \"" + gettext("Indique si cada afirmación es verdadera o falsa") + "\"\n          statements:\n" +
        "            - { text: \"" + gettext("Afirmación verdadera") + "\", answer: true }\n            - { text: \"" + gettext("Afirmación falsa") + "\", answer: false }\n";
    },
    numeric: function () {
      return "  - item:\n      id: " + itemId() + "\n      points: 1\n      questions:\n        - id: q1\n          type: numeric\n          points: 1\n" +
        "          prompt: \"" + gettext("¿Cuánto vale $2^{10}$?") + "\"\n          solution: 1024\n          grading: { atol: 0.5 }\n";
    },
    text: function () {
      return "  - item:\n      id: " + itemId() + "\n      points: 1\n      questions:\n        - id: q1\n          type: text\n          points: 1\n" +
        "          prompt: \"" + gettext("¿Nombre del método?") + "\"\n          grading: { accept: [\"" + gettext("bisección") + "\"] }\n";
    },
  };

  function insert(kind) {
    // Después de la diapositiva del cursor (antes de la siguiente) o al final de las diapositivas.
    var index = currentSlide(), parts = ed.get().split("\n");
    var next = index >= 0 && lines[index + 1] ? lines[index + 1] - 1 : parts.length;
    var before = parts.slice(0, next).join("\n").replace(/\n*$/, "\n");
    var after = parts.slice(next).join("\n");
    var snippet = SNIPPETS[kind]();
    ed.set(before + snippet + after);
    var first = before.split("\n").length;
    ed.select(first, first + snippet.split("\n").length - 1);
    refresh();
  }

  form.querySelectorAll("[data-snippet]").forEach(function (b) {
    b.addEventListener("click", function () {
      insert(b.dataset.snippet);
      b.closest("details").removeAttribute("open");
    });
  });

  // ------------------------------------------------------------------ eventos

  var started = false;
  function start() {
    if (started) return;
    started = true;
    if (!editor) {
      textarea.addEventListener("input", schedule);
      ["click", "keyup"].forEach(function (e) { textarea.addEventListener(e, function () { highlight(true); }); });
      textarea.addEventListener("keydown", function (e) {     // Tab: dos espacios (en YAML la sangría es sintaxis)
        if (e.key === "Tab" && !e.shiftKey) {
          e.preventDefault();
          textarea.setRangeText("  ", textarea.selectionStart, textarea.selectionEnd, "end");
          schedule();
        }
      });
    }
    refresh();
  }

  form.addEventListener("submit", function () { textarea.value = ed.get(); });
  document.addEventListener("keydown", function (e) {
    if (!editor && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); form.requestSubmit(); }
  });
  diag.addEventListener("click", function (e) {
    var li = e.target.closest("li[data-line]");
    if (li && li.dataset.line) ed.goTo(Number(li.dataset.line));
  });
  preview.addEventListener("click", function (e) {
    var card = e.target.closest(".slide-card");
    if (card && lines[Number(card.dataset.index)]) ed.goTo(lines[Number(card.dataset.index)]);
  });

  var toggle = document.getElementById("toggle-preview");
  function setPreview(on) {
    body.classList.toggle("no-preview", !on);
    toggle.setAttribute("aria-pressed", on ? "true" : "false");
    try { localStorage.setItem("policlase.preview", on ? "1" : "0"); } catch (e) {}
  }
  toggle.addEventListener("click", function () { setPreview(body.classList.contains("no-preview")); });
  try { if (localStorage.getItem("policlase.preview") === "0") setPreview(false); } catch (e) {}

  function load(f) {
    if (!f) return;
    if (f.size > 512 * 1024) { window.alert(gettext("El archivo supera 512 KB.")); return; }
    f.text().then(function (content) { ed.set(content); file.value = ""; refresh(); });
  }
  file.addEventListener("change", function () { load(file.files[0]); });
  preview.addEventListener("dragover", function (e) { e.preventDefault(); });
  preview.addEventListener("drop", function (e) { e.preventDefault(); load(e.dataTransfer.files[0]); });

  startMonaco();
})();
