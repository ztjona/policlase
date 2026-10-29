// Paneles laterales del intento y de la revisión del docente: el índice a la izquierda y el estado de
// las preguntas a la derecha. En pantalla ancha cada uno se pliega a su costado (este navegador lo
// recuerda); en pantalla angosta se abren sobre la página con sus botones. El índice resalta la
// parte que se está leyendo. Sin JavaScript, los paneles se ven abiertos.
(function () {
  "use strict";
  var sides = document.querySelectorAll(".attempt-side");
  if (!sides.length) return;

  // Los botones solo sirven con JavaScript: llegan ocultos y se muestran aquí.
  document.querySelectorAll("[data-side-open], [data-side-collapse], [data-side-expand]").forEach(function (b) { b.hidden = false; });

  function setCollapsed(side, collapsed) {
    side.classList.toggle("collapsed", collapsed);
    try { localStorage.setItem("policlase.side." + side.id, collapsed ? "1" : "0"); } catch (e) {}
  }
  sides.forEach(function (side) {
    try { if (localStorage.getItem("policlase.side." + side.id) === "1") side.classList.add("collapsed"); } catch (e) {}
    side.querySelector("[data-side-collapse]").addEventListener("click", function () { setCollapsed(side, true); });
    side.querySelector("[data-side-expand]").addEventListener("click", function () { setCollapsed(side, false); });
  });

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

  // El índice resalta la última entrada cuyo bloque ya pasó por arriba. Se compara la posición en la
  // página, no el número de bloque: con las partes en otro orden (`shuffle_parts`), no van crecientes.
  var tocLinks = Array.from(document.querySelectorAll("[data-toc]"));
  var targets = tocLinks.map(function (a) { return document.getElementById("b-" + a.dataset.toc); });
  var ticking = false;
  function highlight() {
    ticking = false;
    var best = null;
    tocLinks.forEach(function (a, i) { if (targets[i] && targets[i].getBoundingClientRect().top < 140) best = a; });
    tocLinks.forEach(function (a) { if (a === best) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); });
  }
  window.addEventListener("scroll", function () { if (!ticking) { ticking = true; requestAnimationFrame(highlight); } }, { passive: true });
  highlight();
})();
