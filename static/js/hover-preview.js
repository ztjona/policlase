// Vista previa al posar el mouse sobre una miniatura (panel del editor y del proyector).
// Un solo globo flotante fuera de los paneles con scroll, para que nada lo recorte.
window.policlaseHoverPreview = function (container, thumbSelector, htmlFor) {
  "use strict";
  var pop = document.createElement("div");
  pop.className = "hover-preview card";
  pop.hidden = true;
  document.body.appendChild(pop);
  var timer = null, current = null;

  function show(thumb) {
    var html = htmlFor(thumb);
    if (!html) return;
    pop.innerHTML = html;
    if (window.policlaseMath) window.policlaseMath(pop);
    pop.hidden = false;
    var r = thumb.getBoundingClientRect(), h = pop.offsetHeight;
    pop.style.left = (r.right + 10) + "px";
    pop.style.top = Math.max(8, Math.min(r.top, window.innerHeight - h - 8)) + "px";
  }

  container.addEventListener("mouseover", function (e) {
    var thumb = e.target.closest(thumbSelector);
    if (!thumb || thumb === current) return;
    current = thumb;
    clearTimeout(timer);
    timer = setTimeout(function () { show(thumb); }, 250);
  });
  container.addEventListener("mouseleave", function () { current = null; clearTimeout(timer); pop.hidden = true; });
  container.addEventListener("mouseout", function (e) {
    if (current && !current.contains(e.relatedTarget)) { current = null; clearTimeout(timer); pop.hidden = true; }
  });
};
