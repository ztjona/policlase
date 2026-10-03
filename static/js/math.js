// Dibuja con KaTeX las fórmulas que el servidor dejó marcadas.
// El markdown se renderiza en el servidor (dollarmath): aquí solo se toca `.math`.
(function () {
  function renderMath(root) {
    if (!window.katex) return;
    (root || document).querySelectorAll(".math:not([data-katex])").forEach(function (el) {
      // `$$…$$` sale como <div> aunque vaya dentro de un párrafo (dollarmath lo marca
      // "inline"): toda fórmula en <div> es de bloque.
      var display = el.tagName === "DIV" || el.classList.contains("block");
      try {
        window.katex.render(el.textContent, el, { displayMode: display, throwOnError: false, output: "html" });
      } catch (e) { /* se deja el TeX visible */ }
      el.setAttribute("data-katex", "1");
    });
  }
  window.policlaseMath = renderMath;
  document.addEventListener("DOMContentLoaded", function () { renderMath(document); });
})();
