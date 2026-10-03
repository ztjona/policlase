// Arrastrar un .yaml sobre la zona (o sobre el editor) lo carga en el área de texto;
// el servidor valida al guardar y señala línea y código de cada error.
(function () {
  var zone = document.getElementById("dropzone");
  var text = document.getElementById("source");
  var file = document.getElementById("file");
  if (!zone || !text) return;

  function load(f) {
    if (!f) return;
    if (f.size > 512 * 1024) { window.alert(gettext("El archivo supera 512 KB.")); return; }
    f.text().then(function (content) {
      text.value = content;
      file.value = "";                // se envía el texto, no el archivo
      zone.textContent = interpolate(gettext("Cargado: %s — revise y pulse «Validar y guardar»."), [f.name]);
    });
  }

  [zone, text].forEach(function (el) {
    el.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("over"); });
    el.addEventListener("dragleave", function () { zone.classList.remove("over"); });
    el.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("over");
      load(e.dataTransfer.files[0]);
    });
  });
  file.addEventListener("change", function () { load(file.files[0]); });

  // Tab inserta dos espacios: en YAML la sangría es sintaxis.
  text.addEventListener("keydown", function (e) {
    if (e.key !== "Tab" || e.shiftKey) return;
    e.preventDefault();
    var s = text.selectionStart;
    text.setRangeText("  ", s, text.selectionEnd, "end");
  });
})();
