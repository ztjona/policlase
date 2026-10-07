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

  function setStatus(text, kind) {
    status.textContent = text;
    status.className = "status-pill" + (kind ? " " + kind : "");
  }

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
      registerCompletions(monaco);
      editor.onDidChangeCursorPosition(linePreview);
      editor.onDidChangeModelContent(linePreview);
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
    setStatus(gettext("validando…"));
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
        setStatus(gettext("al día"), "ok");
        preview.classList.remove("stale");
        outline.classList.remove("stale");
      } else {
        // Con errores se conserva la última vista válida, atenuada.
        setStatus(interpolate(ngettext("%s error", "%s errores", errors), [errors]), "bad");
        preview.classList.add("stale");
        outline.classList.add("stale");
      }
      highlight(false);
    }).catch(function () { setStatus(gettext("sin conexión"), "bad"); });
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 450); }

  // ------------------------------------------------------------------ panel lateral

  function op(name, index, target) {
    if (outline.classList.contains("stale")) {
      setStatus(gettext("Corrija los errores antes de usar el panel."), "bad");
      return;
    }
    var params = { source: ed.get(), op: name, index: index };
    if (target !== undefined) params.target = target;
    fetch(form.dataset.ops, {
      method: "POST", credentials: "same-origin", body: new URLSearchParams(params),
      headers: { "X-CSRFToken": csrf() },
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.error) { setStatus(data.error, "bad"); return; }
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

  // Comillas simples: en YAML las dobles convierten \t, \f, \b… de LaTeX en caracteres de control.
  function q(text) { return "'" + text.replace(/'/g, "''") + "'"; }
  function head(type) {
    return "  - item:\n      id: " + itemId() + "\n      questions:\n        - id: q1\n          type: " + type + "\n";
  }

  var SNIPPETS = {
    content: function () {
      return "  - markdown: |\n      ## " + gettext("Título") + "\n      " + gettext("Texto con matemáticas: $f(x) = x^2$.") + "\n";
    },
    choice: function () {
      return head("choice") + "          prompt: " + q(gettext("¿Pregunta?")) + "\n          options:\n" +
        "            - { text: " + q(gettext("Correcta")) + ", correct: true }\n" +
        "            - { text: " + q(gettext("Distractor") + " 1") + " }\n            - { text: " + q(gettext("Distractor") + " 2") + " }\n";
    },
    multi_choice: function () {
      return head("multi_choice") + "          prompt: " + q(gettext("Marque todas las correctas")) + "\n          options:\n" +
        "            - { text: " + q(gettext("Correcta") + " 1") + ", correct: true }\n            - { text: " + q(gettext("Correcta") + " 2") + ", correct: true }\n" +
        "            - { text: " + q(gettext("Distractor")) + " }\n";
    },
    true_false: function () {
      return head("true_false") + "          prompt: " + q(gettext("Indique si cada afirmación es verdadera o falsa")) + "\n          statements:\n" +
        "            - { text: " + q(gettext("Afirmación verdadera")) + ", answer: true }\n            - { text: " + q(gettext("Afirmación falsa")) + ", answer: false }\n";
    },
    numeric: function () {
      return head("numeric") + "          prompt: " + q(gettext("¿Cuánto vale $2^{10}$?")) + "\n          solution: 1024\n          grading: { atol: 0.5 }\n";
    },
    text: function () {
      return head("text") + "          prompt: " + q(gettext("¿Nombre del método?")) + "\n          grading: { accept: [" + q(gettext("bisección")) + "] }\n";
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

  // --------------------------------------------- autocompletado según el esquema (Ctrl+Espacio)

  var KEYS = {
    root: { schema: "Versión del formato: policlase.deck/v1", title: "Título de la clase",
            defaults: "Valores por omisión (time_limit_s)", slides: "Lista de diapositivas",
            feedback: "Retroalimentación anónima al final (true por omisión)",
            speed_bonus: "Bono por rapidez en el marcador (true por omisión)", meta: "Datos libres" },
    defaults: { time_limit_s: "Segundos por pregunta (5–600; 30 por omisión)" },
    slides: { markdown: "Contenido: markdown con $matemáticas$", item: "Una pregunta",
              notes: "Notas para el docente", hidden: "true: no se presenta en clase" },
    item: { id: "Identificador único del ítem", points: "Puntos (1 por omisión)", tags: "Etiquetas",
            lecture: "Opciones de clase en vivo (time_limit_s)", stem: "Enunciado común", questions: "La pregunta (una sola en vivo)" },
    lecture: { time_limit_s: "Segundos para esta pregunta (5–600)" },
    questions: { id: "Identificador de la pregunta (q1)", type: "choice · multi_choice · true_false · numeric · text",
                 points: "Puntos (1 por omisión)", prompt: "Enunciado (use comillas simples si lleva LaTeX)",
                 options: "Opciones (choice, multi_choice)", statements: "Afirmaciones (true_false)",
                 solution: "Respuesta (numeric)", grading: "Cómo se califica" },
    options: { text: "Texto de la opción", correct: "true en las correctas" },
    statements: { text: "Afirmación", answer: "true o false" },
    grading: { atol: "numeric: tolerancia absoluta", rtol: "numeric: tolerancia relativa", integer: "numeric: exige entero",
               units: "numeric: unidad obligatoria", accept: "text: respuestas aceptadas",
               normalize: "text: [lowercase, strip_accents, collapse_spaces]",
               partial: "multi_choice/true_false: crédito parcial", penalty: "penalización por error", floor: "mínimo" },
  };
  var VALUES = {
    schema: ["policlase.deck/v1"],
    type: ["choice", "multi_choice", "true_false", "numeric", "text"],
    hidden: ["true", "false"], feedback: ["true", "false"], speed_bonus: ["true", "false"],
    correct: ["true"], answer: ["true", "false"], integer: ["true", "false"],
    partial: ["per_option", "per_statement", "all_or_nothing"],
    normalize: ["[lowercase, strip_accents, collapse_spaces]"],
  };
  var KEY_LINE = /^(\s*)(-\s+)?([A-Za-z_][\w]*)\s*:/;

  function parents(model, lineNumber, keyIndent) {
    var path = [], threshold = keyIndent;
    for (var l = lineNumber - 1; l >= 1 && threshold > 0; l--) {
      var text = model.getLineContent(l);
      if (!text.trim() || text.trim().charAt(0) === "#") continue;
      var m = KEY_LINE.exec(text);
      if (!m) continue;
      var indent = m[1].length, keyAt = indent + (m[2] ? m[2].length : 0);
      if (keyAt < threshold) { path.unshift(m[3]); threshold = m[2] ? indent : keyAt; }
    }
    return path;
  }

  // En variables aparte: xgettext no extrae bien un gettext que comparte línea con «${…}».
  var OPTION_DETAIL = gettext("Opción en una línea");
  var STATEMENT_DETAIL = gettext("Afirmación en una línea");

  function registerCompletions(monaco) {
    monaco.languages.registerCompletionItemProvider("yaml", {
      triggerCharacters: [" ", ":"],
      provideCompletionItems: function (model, position) {
        var before = model.getLineContent(position.lineNumber).slice(0, position.column - 1);
        var word = model.getWordUntilPosition(position);
        var range = { startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
                      startColumn: word.startColumn, endColumn: word.endColumn };
        var value = /^\s*(?:-\s+)?([A-Za-z_]\w*):\s+[\w./-]*$/.exec(before);
        if (value && VALUES[value[1]]) {
          return { suggestions: VALUES[value[1]].map(function (v) {
            return { label: v, kind: monaco.languages.CompletionItemKind.EnumMember, insertText: v, range: range };
          }) };
        }
        var key = /^(\s*)(-\s+)?(\w*)$/.exec(before);
        if (!key) return { suggestions: [] };
        var at = key[1].length + (key[2] ? key[2].length : 0);
        var path = parents(model, position.lineNumber, at);
        var where = path.length ? path[path.length - 1] : "root";
        var keys = KEYS[where] || {};
        var out = Object.keys(keys).map(function (k) {
          return { label: k, kind: monaco.languages.CompletionItemKind.Property, detail: keys[k],
                   insertText: k + ": ", range: range, command: { id: "editor.action.triggerSuggest" } };
        });
        if (where === "options") {
          out.push({ label: "{ text, correct }", kind: monaco.languages.CompletionItemKind.Snippet,
                     insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                     insertText: "{ text: '${1}', correct: ${2|true,false|} }", detail: OPTION_DETAIL, range: range });
        }
        if (where === "statements") {
          out.push({ label: "{ text, answer }", kind: monaco.languages.CompletionItemKind.Snippet,
                     insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                     insertText: "{ text: '${1}', answer: ${2|true,false|} }", detail: STATEMENT_DETAIL, range: range });
        }
        return { suggestions: out };
      },
    });
  }

  // --------------------------------------------- LaTeX de la línea del cursor, renderizado

  var lineBox = document.getElementById("line-preview");
  var MATH = /\$\$[\s\S]+?\$\$|\$[^$]+\$/g;

  function escapeHtml(t) {
    return t.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }

  function linePreview() {
    if (!lineBox || !window.katex) return;
    var line = ed.line(), text = editor ? editor.getModel().getLineContent(line) : "";
    // El valor de la clave (text:, prompt:…) o la línea misma dentro de un bloque markdown.
    var m = /(?:^|[{,\s])(?:text|prompt|markdown|stem)\s*:\s*(['"])(.*?)\1/.exec(text);
    var value = m ? m[2] : text.replace(/^\s*(?:-\s+)?/, "");
    if (value.indexOf("$") < 0) { lineBox.innerHTML = ""; return; }
    var html = "", last = 0;
    value.replace(MATH, function (tex, offset) {
      html += escapeHtml(value.slice(last, offset));
      var display = tex.slice(0, 2) === "$$";
      var body = tex.slice(display ? 2 : 1, display ? -2 : -1);
      try { html += window.katex.renderToString(body, { throwOnError: true, displayMode: false }); }
      catch (err) { html += '<span class="lp-error" title="' + escapeHtml(err.message) + '">' + escapeHtml(tex) + " ⚠</span>"; }
      last = offset + tex.length;
    });
    html += escapeHtml(value.slice(last));
    lineBox.innerHTML = '<span class="lp-label">' + escapeHtml(interpolate(gettext("línea %s"), [line])) + "</span>" + html;
  }

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
