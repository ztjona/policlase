"""Prueba de extremo a extremo de policlase con Chromium real.

Recorre: registro + verificación de correo, solicitud con código, aprobación por el docente,
y una clase en vivo completa con tres estudiantes conectados por SSE.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8100"
PW = "demo-policlase-2026"
SHOTS = Path(__file__).parent / "capturas"
SHOTS.mkdir(exist_ok=True)
COMPOSE_DIR = str(Path(__file__).resolve().parents[2])
STAMP = str(int(time.time()))[-6:]
NEW_USER = f"eva{STAMP}"

results = []


def step(name):
    def deco(fn):
        def run(*a, **k):
            t = time.time()
            try:
                out = fn(*a, **k)
                results.append(("OK", name, time.time() - t))
                print(f"  OK   {name}  ({time.time() - t:.1f}s)", flush=True)
                return out
            except Exception as exc:
                results.append(("FALLA", name, time.time() - t))
                print(f"  FALLA {name}: {exc}", flush=True)
                raise
        return run
    return deco


def login(page, user):
    page.goto(f"{BASE}/cuenta/login/")
    page.fill("input[name=login]", user)
    page.fill("input[name=password]", PW)
    page.click("form button[type=submit]")
    page.wait_for_url(f"{BASE}/")


def latest_confirm_link(email):
    logs = subprocess.run(["docker", "compose", "logs", "--no-log-prefix", "web"],
                          cwd=COMPOSE_DIR, capture_output=True, text=True).stdout
    # El correo en consola viene con saltos de línea "soft" (=\n) si es quoted-printable.
    logs = logs.replace("=\n", "")
    links = re.findall(r"http://[^\s]+/cuenta/confirm-email/[^\s/]+/", logs)
    assert links, "no se encontró el correo de verificación en los logs"
    return links[-1]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        phone = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True,
                 "has_touch": True}
        projector = {"viewport": {"width": 1366, "height": 768}}

        teacher = browser.new_context(**projector).new_page()
        new = browser.new_context(**phone).new_page()
        errors = []
        for pg, who in ((teacher, "docente"), (new, "nuevo")):
            pg.on("pageerror", lambda e, who=who: errors.append(f"{who}: {e}"))

        # ------------------------------------------------------------ 1. registro
        @step("registro de estudiante nuevo")
        def signup():
            new.goto(f"{BASE}/cuenta/signup/")
            new.screenshot(path=SHOTS / "01-registro.png", full_page=True)
            new.fill("input[name=first_name]", "Eva")
            new.fill("input[name=last_name]", "Quishpe")
            new.fill("input[name=student_id]", "1712345678")
            new.fill("input[name=email]", f"{NEW_USER}@example.ec")
            new.fill("input[name=username]", NEW_USER)
            new.fill("input[name=password1]", PW)
            new.fill("input[name=password2]", PW)
            new.click("form button[type=submit]")
            new.wait_for_url(re.compile(r"/cuenta/confirm-email/$"))
        signup()

        @step("sin verificar no entra")
        def blocked():
            new.goto(f"{BASE}/")
            assert "/cuenta/" in new.url, new.url
        blocked()

        @step("verificación por enlace del correo")
        def verify():
            link = latest_confirm_link(f"{NEW_USER}@example.ec")
            new.goto(link.replace("http://localhost:8100", BASE))
            new.click("form button[type=submit]")
            new.wait_for_url(f"{BASE}/")
            expect(new.get_by_role("heading", name="Mis cursos")).to_be_visible()
        verify()

        # ------------------------------------------------- 2. código + aprobación
        login(teacher, "profe")
        teacher.click("text=Métodos Numéricos")
        code = teacher.locator(".joincode").inner_text().strip()

        @step("solicitud con código (en minúsculas y con guion)")
        def request_join():
            new.fill("input[name=code]", code.lower())
            new.click("text=Solicitar inscripción")
            expect(new.locator(".messages")).to_contain_text("Solicitud enviada")
            expect(new.locator(".chip.warn")).to_contain_text("Pendiente")
            new.screenshot(path=SHOTS / "02-estudiante-pendiente.png", full_page=True)
        request_join()

        @step("pendiente no ve el curso")
        def pending_404():
            course_url = teacher.url.replace("/docente/cursos/", "/cursos/")
            r = new.goto(course_url)
            assert r.status == 404, r.status
            new.goto(f"{BASE}/")
        pending_404()

        @step("docente ve 5 pendientes y aprueba todas")
        def approve():
            teacher.reload()
            expect(teacher.locator("button[value=approve_all]")).to_have_text("Aprobar todas (5)")
            teacher.screenshot(path=SHOTS / "03-docente-aprobacion.png", full_page=True)
            teacher.click("button[value=approve_all]")
            expect(teacher.locator(".messages")).to_contain_text("5 solicitud(es) aprobada(s)")
        approve()

        @step("aprobada, el estudiante ve el curso")
        def approved():
            new.reload()
            expect(new.locator(".chip.ok")).to_contain_text("Aprobada")
            new.click("text=Métodos Numéricos")
            expect(new.get_by_role("heading", name="Métodos Numéricos")).to_be_visible()
        approved()

        # ------------------------------------------------------ 3. clase en vivo
        @step("docente inicia la clase y el proyector muestra el PIN")
        def start():
            teacher.goto(teacher.url.split("/docente/")[0] + "/")
            teacher.click("text=Métodos Numéricos")
            teacher.locator("button:has-text('Iniciar clase')").first.click()
            teacher.wait_for_url(re.compile(r"/docente/en-vivo/\d+/$"))
            expect(teacher.locator(".pin")).to_be_visible()
            expect(teacher.locator("#conn")).to_have_text("en vivo")
            return teacher.locator(".pin").inner_text().strip()
        pin = start()

        students = {}
        for name in ("ana", "bruno", "carla"):
            pg = browser.new_context(**phone).new_page()
            pg.on("pageerror", lambda e, n=name: errors.append(f"{n}: {e}"))
            login(pg, name)
            students[name] = pg

        @step("estudiantes entran por PIN y por la página del curso")
        def join():
            for name in ("ana", "bruno"):
                pg = students[name]
                pg.goto(f"{BASE}/en-vivo/")
                pg.fill("input[name=pin]", pin)
                pg.click("button[type=submit]")
                expect(pg.locator("text=Ya estás dentro")).to_be_visible()
            carla = students["carla"]
            carla.goto(f"{BASE}/")
            carla.click("text=Entrar")                       # botón EN VIVO del panel
            carla.click("text=Entrar a la clase")
            expect(carla.locator("text=Ya estás dentro")).to_be_visible()
            students["ana"].screenshot(path=SHOTS / "04-telefono-sala.png")
            expect(teacher.locator(".meter")).to_contain_text("3 estudiantes conectados")
            teacher.screenshot(path=SHOTS / "05-proyector-sala.png")
        join()

        def press(label):
            teacher.locator(f".controls button:has-text('{label}')").click()

        @step("SSE: iniciar y avanzar llega a los teléfonos sin recargar")
        def advance():
            press("Iniciar clase")
            expect(students["ana"].locator(".phone h1")).to_contain_text("Raíces de ecuaciones")
            # KaTeX dibujó las fórmulas de la diapositiva de contenido en el teléfono
            expect(students["ana"].locator(".phone .katex").first).to_be_visible()
            expect(teacher.locator(".slide .katex").first).to_be_visible()
            t0 = time.time()
            press("Siguiente")
            for pg in students.values():
                expect(pg.locator("text=Prepárate")).to_be_visible(timeout=5000)
            print(f"       latencia docente → 3 teléfonos: {time.time() - t0:.2f}s", flush=True)
        advance()

        @step("pregunta de opción única: responder en vivo y conteo en el proyector")
        def answer_choice():
            press("Abrir pregunta")
            for pg in students.values():
                expect(pg.locator("[data-answer-form]")).to_be_visible()
            expect(students["ana"].locator(".timer")).to_have_text(re.compile(r"\d+ s"))
            students["ana"].screenshot(path=SHOTS / "06-telefono-pregunta.png")
            teacher.screenshot(path=SHOTS / "07-proyector-pregunta.png")
            right = "Existe al menos una raíz en el intervalo."
            students["ana"].locator(f"label.option:has-text('{right}')").click()
            students["bruno"].locator(f"label.option:has-text('{right}')").click()
            students["carla"].locator("label.option:has-text('La raíz es única.')").click()
            for pg in students.values():
                pg.click("text=Enviar respuesta")
                expect(pg.locator("text=Respuesta enviada")).to_be_visible()
            expect(teacher.locator(".meter")).to_contain_text("3 de 3 respondieron")
        answer_choice()

        @step("cerrar y revelar: cada estudiante ve su resultado")
        def reveal():
            press("Cerrar ya")
            expect(teacher.locator(".bars")).to_be_visible()
            press("Mostrar respuesta")
            expect(students["ana"].locator(".status-card.ok")).to_contain_text("¡Correcto!")
            expect(students["carla"].locator(".status-card.bad")).to_contain_text("Incorrecto")
            expect(teacher.locator(".board")).to_be_visible()
            students["ana"].screenshot(path=SHOTS / "08-telefono-correcto.png")
            students["carla"].screenshot(path=SHOTS / "09-telefono-incorrecto.png")
            teacher.screenshot(path=SHOTS / "10-proyector-resultados.png")
        reveal()

        @step("pregunta numérica: coma decimal aceptada y cierre automático por tiempo")
        def numeric():
            press("Siguiente")                                # contenido
            press("Siguiente")                                # numérica, 30 s
            press("Abrir pregunta")
            students["ana"].fill("input[name=value]", "10")
            students["ana"].click("text=Enviar respuesta")
            students["bruno"].fill("input[name=value]", "10,0")
            students["bruno"].click("text=Enviar respuesta")
            # carla no responde: la pregunta debe cerrarse sola al vencer el tiempo
            expect(students["carla"].locator("text=No alcanzaste a responder")).to_be_visible(timeout=40000)
            expect(teacher.locator("button:has-text('Mostrar respuesta')")).to_be_visible()
            press("Mostrar respuesta")
            expect(students["bruno"].locator(".status-card.ok")).to_be_visible()
        numeric()

        @step("selección múltiple: marcar todo penaliza")
        def multi():
            press("Siguiente")
            press("Abrir pregunta")
            for label in ("continua", "signos opuestos"):
                students["ana"].locator(f"label.option:has-text('{label}')").click()
            for label in ("continua", "signos opuestos", "derivable"):
                students["bruno"].locator(f"label.option:has-text('{label}')").click()
            for n in ("ana", "bruno"):
                students[n].click("text=Enviar respuesta")
                expect(students[n].locator("text=Respuesta enviada")).to_be_visible()
            press("Cerrar ya")
            press("Mostrar respuesta")
            expect(students["ana"].locator(".status-card")).to_contain_text("¡Correcto!")
            expect(students["bruno"].locator(".status-card")).to_contain_text("Parcialmente")
        multi()

        @step("verdadero/falso con teclado del presentador (flecha y espacio)")
        def tf():
            teacher.keyboard.press("ArrowRight")
            expect(students["ana"].locator("text=Prepárate")).to_be_visible()
            teacher.keyboard.press("Space")                  # abrir
            ana = students["ana"]
            expect(ana.locator(".tf-row")).to_have_count(2)
            rows = ana.locator(".tf-row")
            rows.nth(0).locator("label:has-text('Verdadero')").click()
            rows.nth(1).locator("label:has-text('Falso')").click()
            ana.screenshot(path=SHOTS / "11-telefono-vf.png")
            ana.click("text=Enviar respuesta")
            expect(ana.locator("text=Respuesta enviada")).to_be_visible()
            teacher.keyboard.press("Space")                  # cerrar
            teacher.keyboard.press("Space")                  # revelar
            expect(ana.locator(".status-card")).to_contain_text("¡Correcto!")
        tf()

        @step("terminar: puntaje final y resultados del docente")
        def finish():
            teacher.once("dialog", lambda d: d.accept())
            press("Terminar")
            expect(teacher.get_by_role("heading", name="Clase terminada")).to_be_visible()
            expect(students["ana"].get_by_role("heading", name="Clase terminada")).to_be_visible()
            expect(students["ana"].locator(".status-card")).to_contain_text("puesto 1")
            students["ana"].screenshot(path=SHOTS / "12-telefono-final.png")
            teacher.click("text=Ver resultados completos")
            expect(teacher.locator("table")).to_contain_text("Pérez")
            expect(teacher.locator("table")).to_contain_text("ausente")   # diego y eva no entraron
            teacher.screenshot(path=SHOTS / "13-resultados.png", full_page=True)
        finish()

        @step("sin errores de JavaScript en ninguna página")
        def no_js_errors():
            assert not errors, errors
        no_js_errors()

        browser.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        ok = sum(1 for r in results if r[0] == "OK")
        print(f"\n{ok}/{len(results)} pasos correctos")
        sys.exit(0 if ok == len(results) else 1)
