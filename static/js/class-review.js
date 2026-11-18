// Repaso de una clase (live/review.html): una diapositiva a la vez, con «Anterior» y «Siguiente», las
// flechas del teclado y el índice. La posición queda en la dirección (#d3), así que recargar la página la
// conserva; se reemplaza en vez de agregarse, para que «Atrás» del navegador vuelva al curso y no recorra
// las diapositivas. Sin JavaScript, las diapositivas se ven una debajo de otra y el índice salta a cada una.
(function () {
  "use strict";
  var root = document.querySelector("[data-review]");
  var nav = document.querySelector("[data-review-nav]");
  if (!root || !nav) return;
  var slides = Array.prototype.slice.call(root.querySelectorAll("[data-slide]"));
  if (!slides.length) return;
  var prev = nav.querySelector("[data-prev]");
  var next = nav.querySelector("[data-next]");
  var pos = nav.querySelector("[data-pos]");
  var links = Array.prototype.slice.call(document.querySelectorAll("[data-goto]"));
  var current = -1;

  function fromHash() {
    var m = /^#d(\d+)$/.exec(window.location.hash);
    return (m ? parseInt(m[1], 10) : 1) - 1;
  }

  // `initial`: al cargar no se toca la dirección; si se pusiera el ancla entonces, el navegador
  // bajaría hasta ella al terminar de cargar y el título de la clase quedaría fuera de la vista.
  function show(i, initial) {
    i = Math.min(Math.max(i, 0), slides.length - 1);
    if (i === current) return;
    current = i;
    slides.forEach(function (s, k) { s.hidden = k !== i; });
    prev.disabled = i === 0;
    next.disabled = i === slides.length - 1;
    pos.textContent = interpolate(gettext("Diapositiva %(n)s de %(total)s"),
                                  { n: i + 1, total: slides.length }, true);
    links.forEach(function (a) {
      if (parseInt(a.dataset.goto, 10) === i + 1) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    var hash = "#d" + (i + 1);
    if (!initial && window.location.hash !== hash) history.replaceState(null, "", hash);
    // Tras bajar por una diapositiva larga, la siguiente empieza desde arriba.
    if (root.getBoundingClientRect().top < 0) root.scrollIntoView({ block: "start" });
  }

  root.classList.add("one-by-one");
  nav.hidden = false;
  show(fromHash(), true);

  prev.addEventListener("click", function () { show(current - 1); });
  next.addEventListener("click", function () { show(current + 1); });
  links.forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();                   // el panel en pantalla angosta se cierra igual (side-panels.js)
      show(parseInt(a.dataset.goto, 10) - 1);
    });
  });
  window.addEventListener("hashchange", function () { show(fromHash()); });
  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    // Solo las flechas laterales: Re Pág, Av Pág, Inicio y Fin siguen desplazando la página.
    var step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (step) { e.preventDefault(); show(current + step); }
  });
})();
