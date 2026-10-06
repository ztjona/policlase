// Editor de presentaciones: YAML a la izquierda, diapositivas a la derecha.
// El servidor valida y renderiza (deck_preview) mientras se escribe; la diapositiva donde está
// el cursor se resalta en la vista previa. Los errores llevan a su línea con un clic.
(function () {
  "use strict";
  var form = document.getElementById("deck-form");
  var text = document.getElementById("source");
  var file = document.getElementById("file");
  var preview = document.getElementById("preview");
  var status = document.getElementById("preview-status");
  var diag = document.getElementById("diagnostics");
  if (!form || !text) return;

  var lines = [];        // línea (1-based) donde empieza cada diapositiva
  var timer = null, seq = 0;

  function csrf() { return form.querySelector("input[name=csrfmiddlewaretoken]").value; }

  function cursorLine() {
    return text.value.slice(0, text.selectionStart).split("\n").length;
  }

  function currentSlide() {
    var line = cursorLine(), index = -1;
    lines.forEach(function (start, i) { if (start && start <= line) index = i; });
    return index;
  }

  function highlight(scroll) {
    var index = currentSlide();
    preview.querySelectorAll(".slide-card").forEach(function (card) {
      var on = Number(card.dataset.index) === index;
      card.classList.toggle("current", on);
      if (on && scroll) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }

  function goToLine(line) {
    var parts = text.value.split("\n"), pos = 0;
    for (var i = 0; i < line - 1 && i < parts.length; i++) pos += parts[i].length + 1;
    text.focus();
    text.setSelectionRange(pos, pos + (parts[line - 1] || "").length);
    // Desplaza el área de texto hasta la línea (aprox.: altura de línea constante).
    var lh = parseFloat(getComputedStyle(text).lineHeight) || 19;
    text.scrollTop = Math.max(0, (line - 5) * lh);
    highlight(true);
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
  }

  function refresh() {
    var mine = ++seq;
    status.textContent = gettext("validando…");
    var body = new URLSearchParams({ source: text.value });
    fetch(form.dataset.preview, {
      method: "POST", credentials: "same-origin", body: body, headers: { "X-CSRFToken": csrf() },
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (mine !== seq) return;                       // llegó una respuesta más nueva
      lines = data.lines || [];
      showDiagnostics(data.diagnostics || []);
      var errors = (data.diagnostics || []).filter(function (d) { return d.severity === "error"; }).length;
      if (data.html !== null && data.html !== undefined) {
        preview.innerHTML = data.html;
        if (window.policlaseMath) window.policlaseMath(preview);
        status.textContent = gettext("al día");
        preview.classList.remove("stale");
      } else {
        // Con errores se conserva la última vista válida, atenuada.
        status.textContent = interpolate(ngettext("%s error", "%s errores", errors), [errors]);
        preview.classList.add("stale");
      }
      highlight(false);
    }).catch(function () { status.textContent = gettext("sin conexión"); });
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 450); }

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
    // Después de la diapositiva del cursor (antes de la siguiente) o al final del documento.
    var index = currentSlide(), parts = text.value.split("\n");
    var next = index >= 0 && lines[index + 1] ? lines[index + 1] - 1 : parts.length;
    var before = parts.slice(0, next).join("\n").replace(/\n*$/, "\n");
    var after = parts.slice(next).join("\n");
    var snippet = SNIPPETS[kind]();
    text.value = before + snippet + (after ? after : "");
    var pos = before.length;
    text.focus();
    text.setSelectionRange(pos, pos + snippet.length);
    refresh();
  }

  form.querySelectorAll("[data-snippet]").forEach(function (b) {
    b.addEventListener("click", function () { insert(b.dataset.snippet); });
  });

  // ------------------------------------------------------------------ eventos

  text.addEventListener("input", schedule);
  ["click", "keyup"].forEach(function (e) { text.addEventListener(e, function () { highlight(true); }); });
  diag.addEventListener("click", function (e) {
    var li = e.target.closest("li[data-line]");
    if (li && li.dataset.line) goToLine(Number(li.dataset.line));
  });
  preview.addEventListener("click", function (e) {
    var card = e.target.closest(".slide-card");
    if (card && lines[Number(card.dataset.index)]) goToLine(lines[Number(card.dataset.index)]);
  });

  // Tab inserta dos espacios: en YAML la sangría es sintaxis.
  text.addEventListener("keydown", function (e) {
    if (e.key === "Tab" && !e.shiftKey) {
      e.preventDefault();
      text.setRangeText("  ", text.selectionStart, text.selectionEnd, "end");
      schedule();
    }
  });
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); form.requestSubmit(); }
  });

  function load(f) {
    if (!f) return;
    if (f.size > 512 * 1024) { window.alert(gettext("El archivo supera 512 KB.")); return; }
    f.text().then(function (content) {
      text.value = content;
      file.value = "";                // se envía el texto, no el archivo
      refresh();
    });
  }
  [text, preview].forEach(function (el) {
    el.addEventListener("dragover", function (e) { e.preventDefault(); });
    el.addEventListener("drop", function (e) { e.preventDefault(); load(e.dataTransfer.files[0]); });
  });
  file.addEventListener("change", function () { load(file.files[0]); });

  refresh();
})();
