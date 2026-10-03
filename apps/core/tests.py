from django.test import SimpleTestCase

from . import markdown


class MarkdownTests(SimpleTestCase):
    def test_latex_sobrevive_a_markdown(self):
        html = markdown.render(r"Sea $a_1 * b_2$ y $$\sum_{i=1}^{n} x_i$$")
        self.assertIn('class="math inline"', html)
        self.assertIn("a_1 * b_2", html)            # ni _ ni * se convirtieron en énfasis
        self.assertIn('<div class="math', html)   # $$…$$ va en <div>: math.js lo dibuja en bloque

    def test_html_hostil_se_descarta(self):
        for hostile in ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>',
                        '[x](javascript:alert(1))', '<iframe src="https://x"></iframe>'):
            html = markdown.render(hostile)
            # El HTML crudo queda escapado como texto inerte; ninguna etiqueta ni enlace activo.
            for tag in ("<script", "<img", "<iframe", 'href="javascript'):
                self.assertNotIn(tag, html)

    def test_inline_sin_parrafo(self):
        self.assertNotIn("<p>", markdown.render_inline("**hola** $x$"))
