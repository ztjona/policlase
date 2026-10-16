from django.test import SimpleTestCase

from . import markdown


class MarkdownTests(SimpleTestCase):
    def test_markdown_del_estudiante_sin_enlaces_ni_imagenes(self):
        html = markdown.render_student("**Porque** $x^2$\nsegunda línea\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
                                       "[enlace](http://x) ![foto](http://x/y.png) <script>alert(1)</script>")
        self.assertIn("<strong>Porque</strong>", html)
        self.assertIn('<span class="math inline">', html)
        self.assertIn("<br", html)                                  # el salto de línea se respeta
        self.assertIn("<table>", html)
        self.assertNotIn("<a ", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertIn("enlace", html)

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


class StudentMathTests(SimpleTestCase):
    """Lo que escribe un estudiante: sus fórmulas se dibujan, su HTML nunca."""

    def test_formulas_marcadas_y_html_escapado(self):
        from .templatetags.policlase import student_math

        html = student_math("Sea $x^2 < 1$\n<img src=x onerror=alert(1)> y $$\\int_0^1 x\\,dx$$ [enlace](http://x)")
        self.assertIn('<span class="math inline">x^2 &lt; 1</span>', html)
        self.assertIn('<span class="math block">\\int_0^1 x\\,dx</span>', html)
        self.assertIn("&lt;img", html)
        self.assertNotIn("<img", html)
        self.assertIn("<br>", html)
        self.assertIn("[enlace](http://x)", html)                 # sin markdown: ni enlaces ni imágenes
        self.assertEqual(student_math("cuesta $5 y $6"), "cuesta <span class=\"math inline\">5 y </span>6")
