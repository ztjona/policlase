"""Prueba de carga: una clase en vivo con N estudiantes simulados.

    pip install httpx
    POLICLASE_URL=https://su-dominio POLICLASE_CLAVE=... python carga.py 30

Los estudiantes entran como invitados («Carga 01», …). Al terminar, bórrelos junto con la sesión
(el número se imprime al final): ver README.md.

Cada estudiante es un invitado con su propio flujo SSE; al recibir un cambio pide su fragmento
(como live.js) y responde tras una demora aleatoria. El docente maneja la clase por HTTP.
Mide: propagación de cada acción del docente a todos los teléfonos, latencia de las respuestas,
cierre automático y retroalimentación.
"""
import asyncio
import json
import random
import re
import statistics
import sys
import time

import os

import httpx

# Configuración: el docente y la presentación con que se dicta la clase simulada.
B = os.environ.get("POLICLASE_URL", "http://127.0.0.1:8100").rstrip("/")
TEACHER = os.environ.get("POLICLASE_DOCENTE", "profe")
PASSWORD = os.environ["POLICLASE_CLAVE"]
COURSE = int(os.environ.get("POLICLASE_CURSO", "1"))
DECK = int(os.environ.get("POLICLASE_PRESENTACION", "1"))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')

events: list[tuple[int, int, float]] = []          # (estudiante, state_version, t)
answer_lat: list[float] = []
frag_lat: list[float] = []
errors: list[str] = []
feedback_sent = 0


def csrf(html: str) -> str:
    return TOKEN.search(html).group(1)


async def login_teacher(c: httpx.AsyncClient):
    r = await c.get(B + "/cuenta/login/")
    await c.post(B + "/cuenta/login/", data={"csrfmiddlewaretoken": csrf(r.text), "login": TEACHER,
                                              "password": PASSWORD},
                 headers={"Referer": B + "/cuenta/login/"})


def pick_answer(html: str):
    form = re.search(r'<form method="post" action="([^"]+)" data-answer-form data-slide="(\w+)"', html)
    if not form:
        return None
    action, slide = form.groups()
    data = {"csrfmiddlewaretoken": csrf(html)}
    if slide == "feedback":
        data.update(rating=str(random.randint(3, 5)), comment=random.choice(["", "Bien explicado", "Más ejemplos"]))
        return action, slide, data
    choices = re.findall(r'name="choice" value="([^"]+)"', html)
    tf = sorted(set(re.findall(r'name="(tf_[^"]+)"', html)))
    if choices:
        multi = 'type="checkbox"' in html
        data["choice"] = random.sample(choices, k=random.randint(1, 2)) if multi else random.choice(choices)
    elif tf:
        data.update({k: random.choice("01") for k in tf})
    elif 'name="value"' in html:
        data["value"] = random.choice(["10", "9", "10,0"])
    return action, slide, data


async def student(i: int, url: str, session_pk: int, stop: asyncio.Event):
    async with httpx.AsyncClient(timeout=30, http2=False) as c:
        r = await c.get(url)
        r = await c.post(url, data={"csrfmiddlewaretoken": csrf(r.text), "name": f"Carga {i:02d}"},
                         headers={"Referer": url}, follow_redirects=True)
        if r.status_code != 200:
            errors.append(f"entrar {i}: {r.status_code}")
            return
        answered: set[str] = set()
        busy = asyncio.Lock()

        async def handle():
            global feedback_sent
            async with busy:
                t0 = time.monotonic()
                r = await c.get(f"{B}/en-vivo/{session_pk}/escena/")
                frag_lat.append(time.monotonic() - t0)
                picked = pick_answer(r.text)
                if not picked or picked[1] in answered:
                    return
                action, slide, data = picked
                answered.add(slide)
            await asyncio.sleep(random.uniform(1.0, 6.0))           # pensar
            t0 = time.monotonic()
            r = await c.post(B + action, data=data, headers={"Referer": url, "X-CSRFToken": data["csrfmiddlewaretoken"]})
            answer_lat.append(time.monotonic() - t0)
            if r.status_code != 200 or 'class="error"' in r.text:
                errors.append(f"respuesta {i} {slide}: {r.status_code} {re.findall(r'class=\"error\">([^<]+)', r.text)}")
            elif slide == "feedback":
                feedback_sent += 1

        last = None
        try:
            async with c.stream("GET", f"{B}/en-vivo/{session_pk}/eventos/", timeout=None) as s:
                async for line in s.aiter_lines():
                    if stop.is_set():
                        break
                    if not line.startswith("data: "):
                        continue
                    snap = json.loads(line[6:])
                    events.append((i, snap["state_version"], time.time()))
                    key = (snap["state_version"], snap["status"])
                    if key != last:
                        last = key
                        asyncio.create_task(handle())
                    if snap["status"] == "ended":
                        await asyncio.sleep(8)                         # tiempo para la retroalimentación
                        break
        except httpx.HTTPError as exc:
            errors.append(f"sse {i}: {exc!r}")


async def main():
    stop = asyncio.Event()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as t:
        await login_teacher(t)
        r = await t.get(f"{B}/docente/cursos/{COURSE}/")
        r = await t.post(f"{B}/docente/cursos/{COURSE}/presentaciones/{DECK}/iniciar/",
                         data={"csrfmiddlewaretoken": csrf(r.text)}, headers={"Referer": B + "/"})
        session_pk = int(re.search(r"/docente/en-vivo/(\d+)/", str(r.url)).group(1))
        token = csrf(r.text)
        hdr = {"X-CSRFToken": token, "Referer": str(r.url)}

        async def act(action):
            t0 = time.time()
            res = await t.post(f"{B}/docente/en-vivo/{session_pk}/accion/", data={"action": action}, headers=hdr)
            return t0, res.status_code

        async def stage():
            return (await t.get(f"{B}/docente/en-vivo/{session_pk}/escena/")).text

        await act("guests_on")
        url = re.search(r'class="mono guest-url">([^<]+)<', await stage()).group(1).strip()
        print(f"sesión {session_pk}; entrando {N} estudiantes…", flush=True)
        tasks = [asyncio.create_task(student(i, url, session_pk, stop)) for i in range(1, N + 1)]
        await asyncio.sleep(6)
        connected = re.search(r"(\d+) estudiantes? conectados?", await stage())
        print("conectados en la sala:", connected.group(1) if connected else "?", flush=True)

        actions = []
        actions.append(("start", *await act("start")))
        await asyncio.sleep(2)
        closes = []
        for _ in range(8):
            html = await stage()
            phase = re.search(r'data-phase="(\w+)"', html).group(1)
            if phase == "ready":
                actions.append(("open", *await act("open")))
                t_open = time.time()
                while True:
                    await asyncio.sleep(0.25)
                    html = await stage()
                    if 'data-phase="closed"' in html:
                        closes.append((time.time() - t_open, "Respondieron todos" in html))
                        break
                    if time.time() - t_open > 40:
                        closes.append((None, False))
                        break
                actions.append(("reveal", *await act("reveal")))
                await asyncio.sleep(1.5)
            if 'data-action="next"' in html and 'disabled title' not in html.split('data-action="next"')[1][:80]:
                actions.append(("next", *await act("next")))
                await asyncio.sleep(1.5)
            else:
                break
        await asyncio.sleep(9)                                         # retroalimentación (diapositiva final)
        actions.append(("end", *await act("end")))
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=40)
        stop.set()
        results = (await t.get(f"{B}/docente/en-vivo/{session_pk}/resultados/")).text

    # Propagación: para cada acción, cuándo vio cada estudiante el primer evento posterior.
    print("\nacción   status  propagación a los teléfonos (p50 / p95 / máx, s)")
    for name, t0, code in actions:
        delays = []
        for i in range(1, N + 1):
            after = [t for (s, _v, t) in events if s == i and t >= t0]
            if after:
                delays.append(min(after) - t0)
        if delays:
            q = statistics.quantiles(delays, n=20)
            print(f"{name:8} {code}     {statistics.median(delays):.2f} / {q[18]:.2f} / {max(delays):.2f}  ({len(delays)}/{N})")
    print("\ncierres automáticos (s desde abrir, ¿«respondieron todos»?):", [(round(s, 1) if s else None, ok) for s, ok in closes])
    for label, data in (("fragmento", frag_lat), ("respuesta", answer_lat)):
        if data:
            q = statistics.quantiles(data, n=20)
            print(f"latencia {label}: p50 {statistics.median(data)*1000:.0f} ms · p95 {q[18]*1000:.0f} ms · máx {max(data)*1000:.0f} ms · n={len(data)}")
    print("retroalimentación enviada:", feedback_sent, "| en resultados:", re.findall(r"(\d+) respuestas", results)[:1])
    print("errores:", len(errors), errors[:8])
    print("SESSION", session_pk)


asyncio.run(main())
