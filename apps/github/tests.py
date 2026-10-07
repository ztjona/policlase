import hashlib
import hmac
import json
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.courses.models import Course
from apps.live.models import Deck, LiveSession, Section

from . import sync
from .client import Blob, Conflict, GitHubError
from .models import CourseRepo, GitHubAccount

DEMO = (Path(__file__).resolve().parents[1] / "live" / "demo" / "biseccion-clase-1.yaml").read_text(encoding="utf-8")
SMALL = 'schema: policlase.deck/v1\ntitle: "{title}"\nslides:\n  - markdown: "# Hola"\n'
BROKEN = "schema: policlase.deck/v1\ntitle: Rota\nslides: []\n"


def sha_of(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


class FakeGitHub:
    """Un repositorio en memoria con la interfaz de GitHubClient."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.commits = []
        self.fail = None

    def login(self):
        return "profe-gh"

    def check_repo(self, repo, branch):
        if repo != "profe/cursos":
            raise GitHubError("Sin acceso", 404)

    def tree(self, repo, branch):
        if self.fail:
            raise self.fail
        return {path: sha_of(text) for path, text in self.files.items()}

    def read(self, repo, sha):
        return next(t for t in self.files.values() if sha_of(t) == sha)

    def write(self, repo, branch, path, text, message, sha=""):
        current = self.files.get(path)
        if (current is None and sha) or (current is not None and sha_of(current) != sha):
            raise Conflict("cambió", 409)
        self.files[path] = text
        self.commits.append(message)
        return sha_of(text)

    def commit(self, repo, branch, changes, message, expected=None):
        for path, sha in (expected or {}).items():
            if sha_of(self.files.get(path, "")) != sha:
                raise Conflict("cambió", 409)
        blobs = {sha_of(t): t for t in self.files.values()}
        new = dict(self.files)
        for path, value in changes.items():
            if value is None:
                new.pop(path, None)
            elif isinstance(value, Blob):
                new[path] = blobs[value.sha]
            else:
                new[path] = value
        self.files = new
        self.commits.append(message)
        return {path: sha_of(new[path]) for path, value in changes.items() if value is not None}

    def delete(self, repo, branch, path, message, sha):
        if sha_of(self.files.get(path, "")) != sha:
            raise Conflict("cambió", 409)
        del self.files[path]
        self.commits.append(message)


def make_user(username, role=User.Role.TEACHER):
    return User.objects.create_user(username, f"{username}@example.ec", "clave-segura-2026", role=role)


class SyncBase(TestCase):
    def setUp(self):
        self.teacher = make_user("profe")
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")
        account = GitHubAccount(user=self.teacher, login="profe-gh")
        account.token = "github_pat_secreto"
        account.save()
        self.gh = FakeGitHub({"mn/clases/biseccion.yaml": DEMO, "mn/otra-cosa.yaml": DEMO,
                              "mn/clases/items.yaml": "schema: policlase.item/v1\nid: x\n"})
        patcher = mock.patch("apps.github.sync.client_for", return_value=self.gh)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.link = CourseRepo.objects.create(course=self.course, repo="profe/cursos", folder="mn/clases")


class PullTests(SyncBase):
    def test_trae_solo_presentaciones_de_la_carpeta(self):
        result = sync.pull(self.link)
        self.assertEqual(result.created, ["mn/clases/biseccion.yaml"])
        deck = Deck.objects.get()
        self.assertEqual((deck.github_path, deck.github_sha), ("mn/clases/biseccion.yaml", sha_of(DEMO)))

    def test_sin_cambios_no_descarga_de_nuevo(self):
        sync.pull(self.link)
        with mock.patch.object(self.gh, "read", side_effect=AssertionError("descargó")):
            self.assertFalse(sync.pull(self.link).changed)

    def test_actualiza_y_borra(self):
        sync.pull(self.link)
        self.gh.files["mn/clases/biseccion.yaml"] = SMALL.format(title="Nueva versión")
        self.assertEqual(sync.pull(self.link).updated, ["mn/clases/biseccion.yaml"])
        self.assertEqual(Deck.objects.get().title, "Nueva versión")
        del self.gh.files["mn/clases/biseccion.yaml"]
        self.assertEqual(sync.pull(self.link).deleted, ["mn/clases/biseccion.yaml"])
        self.assertFalse(Deck.objects.exists())

    def test_archivo_con_errores_conserva_la_version_valida(self):
        sync.pull(self.link)
        self.gh.files["mn/clases/biseccion.yaml"] = BROKEN
        result = sync.pull(self.link)
        self.assertEqual(result.issues[0]["path"], "mn/clases/biseccion.yaml")
        self.assertEqual(Deck.objects.get().title, "Bisección — clase 1")
        self.link.refresh_from_db()
        self.assertTrue(self.link.issues)

    def test_error_de_github_queda_anotado_y_no_rompe_el_curso(self):
        self.gh.fail = GitHubError("GitHub caído")
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("course_manage", args=[self.course.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GitHub caído")

    def test_abrir_el_curso_sincroniza(self):
        self.client.force_login(self.teacher)
        self.client.get(reverse("course_manage", args=[self.course.pk]))
        self.assertTrue(Deck.objects.exists())


class PushTests(SyncBase):
    def setUp(self):
        super().setUp()
        sync.pull(self.link)
        self.deck = Deck.objects.get()
        self.client.force_login(self.teacher)

    def save(self, source, deck=None):
        url = (reverse("deck_edit", args=[self.course.pk, deck.pk]) if deck
               else reverse("deck_new", args=[self.course.pk]))
        return self.client.post(url, {"source": source})

    def test_guardar_confirma_en_github(self):
        self.save(SMALL.format(title="Editada en la web"), self.deck)
        self.assertEqual(self.gh.files["mn/clases/biseccion.yaml"], SMALL.format(title="Editada en la web"))
        self.deck.refresh_from_db()
        self.assertEqual(self.deck.github_sha, sha_of(SMALL.format(title="Editada en la web")))

    def test_nueva_presentacion_crea_archivo(self):
        self.save(SMALL.format(title="Método de Newton"))
        self.assertIn("mn/clases/metodo-de-newton.yaml", self.gh.files)

    def test_conflicto_no_pisa_github_ni_pierde_el_texto(self):
        self.gh.files["mn/clases/biseccion.yaml"] = SMALL.format(title="Cambio en GitHub")
        mine = SMALL.format(title="Cambio en la web")
        response = self.save(mine, self.deck)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cambio en la web")              # sigue en el editor
        self.assertEqual(self.gh.files["mn/clases/biseccion.yaml"], SMALL.format(title="Cambio en GitHub"))
        self.deck.refresh_from_db()
        self.assertEqual(self.deck.title, "Bisección — clase 1")

    def test_eliminar_borra_en_github(self):
        self.client.post(reverse("deck_delete", args=[self.course.pk, self.deck.pk]))
        self.assertNotIn("mn/clases/biseccion.yaml", self.gh.files)
        self.assertFalse(Deck.objects.exists())

    def test_las_clases_dictadas_sobreviven_al_borrado_en_github(self):
        session = LiveSession.objects.create(course=self.course, deck=self.deck, title="x", slides=self.deck.slides)
        del self.gh.files["mn/clases/biseccion.yaml"]
        sync.pull(self.link)
        session.refresh_from_db()
        self.assertEqual(len(session.slides), len(self.deck.slides))


class LinkTests(TestCase):
    def setUp(self):
        self.teacher = make_user("profe")
        self.other = make_user("otra")
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")
        self.gh = FakeGitHub()
        patcher = mock.patch("apps.github.sync.client_for", return_value=self.gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_conectar_token_lo_guarda_cifrado(self):
        self.client.force_login(self.teacher)
        with mock.patch("apps.github.views.GitHubClient") as client:
            client.return_value.login.return_value = "profe-gh"
            self.client.post(reverse("github_account"), {"token": "github_pat_abc"})
        account = GitHubAccount.objects.get(user=self.teacher)
        self.assertEqual((account.login, account.token), ("profe-gh", "github_pat_abc"))
        self.assertNotIn("github_pat_abc", account.token_encrypted)

    def test_vincular_sube_las_presentaciones_locales(self):
        GitHubAccount.objects.create(user=self.teacher, login="x", token_encrypted="")
        Deck.objects.create(course=self.course, title="Local", source=SMALL.format(title="Local"),
                            compiled={"title": "Local", "slides": []})
        self.client.force_login(self.teacher)
        self.client.post(reverse("github_course_link", args=[self.course.pk]),
                         {"action": "link", "repo": "https://github.com/profe/cursos", "branch": "main",
                          "folder": "/mn/clases/"})
        link = CourseRepo.objects.get()
        self.assertEqual((link.repo, link.folder), ("profe/cursos", "mn/clases"))
        self.assertIn("mn/clases/local.yaml", self.gh.files)
        self.assertEqual(Deck.objects.get().github_path, "mn/clases/local.yaml")

    def test_otro_docente_no_vincula_ni_sincroniza_cursos_ajenos(self):
        self.client.force_login(self.other)
        url = reverse("github_course_link", args=[self.course.pk])
        self.assertEqual(self.client.post(url, {"action": "link", "repo": "profe/cursos"}).status_code, 404)
        self.assertEqual(self.client.post(url, {"action": "sync"}).status_code, 404)
        self.assertFalse(CourseRepo.objects.exists())

    def test_estudiante_no_conecta_github(self):
        self.client.force_login(make_user("ana", User.Role.STUDENT))
        self.assertEqual(self.client.get(reverse("github_account")).status_code, 403)


class WebhookTests(SyncBase):
    def post(self, body: dict, secret: str, event="push"):
        raw = json.dumps(body).encode()
        signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return self.client.post(reverse("github_webhook", args=[self.course.pk]), raw,
                                content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=signature, HTTP_X_GITHUB_EVENT=event)

    def test_push_firmado_sincroniza(self):
        self.assertEqual(self.post({"ref": "refs/heads/main"}, self.link.webhook_secret).status_code, 204)
        self.assertTrue(Deck.objects.exists())

    def test_firma_invalida_se_rechaza(self):
        self.assertEqual(self.post({"ref": "refs/heads/main"}, "otro-secreto").status_code, 403)
        self.assertFalse(Deck.objects.exists())

    def test_otra_rama_no_sincroniza(self):
        self.post({"ref": "refs/heads/borrador"}, self.link.webhook_secret)
        self.assertFalse(Deck.objects.exists())


class SectionSyncTests(SyncBase):
    """Cada carpeta con presentaciones es una sección; las demás carpetas se ignoran."""

    def setUp(self):
        super().setUp()
        self.gh.files = {
            "mn/clases/unidad-02/newton.yaml": SMALL.format(title="Newton"),
            "mn/clases/unidad-01/biseccion.yaml": DEMO,
            "mn/clases/unidad-01/regula.yaml": SMALL.format(title="Regula falsi"),
            "mn/clases/suelta.yaml": SMALL.format(title="Suelta"),
            "mn/clases/generators/gen.py": "print(1)",
            "mn/clases/items/x.yaml": "schema: policlase.item/v1\nid: x\n",
            "mn/clases/unidad-10/.policlase-seccion": "",
        }
        sync.pull(self.link)

    def test_carpetas_con_presentaciones_son_secciones_en_orden_natural(self):
        self.assertEqual(list(Section.objects.values_list("title", flat=True)),
                         ["Unidad 01", "Unidad 02", "Unidad 10"])
        unit1 = Section.objects.get(github_folder="unidad-01")
        self.assertEqual(sorted(unit1.decks.values_list("title", flat=True)), ["Bisección — clase 1", "Regula falsi"])
        self.assertIsNone(Deck.objects.get(title="Suelta").section)

    def test_carpeta_borrada_en_github_borra_la_seccion(self):
        del self.gh.files["mn/clases/unidad-02/newton.yaml"]
        sync.pull(self.link)
        self.assertFalse(Section.objects.filter(github_folder="unidad-02").exists())

    def test_crear_seccion_en_la_web_crea_la_carpeta(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("section_create", args=[self.course.pk]), {"title": "Unidad 3 - Interpolación"})
        self.assertIn("mn/clases/Unidad 3 - Interpolación/.policlase-seccion", self.gh.files)
        sync.pull(self.link)
        self.assertTrue(Section.objects.filter(title="Unidad 3 - Interpolación").exists())

    def test_renombrar_mueve_todos_los_archivos_en_un_commit(self):
        unit1 = Section.objects.get(github_folder="unidad-01")
        self.client.force_login(self.teacher)
        commits = len(self.gh.commits)
        self.client.post(reverse("section_rename", args=[self.course.pk, unit1.pk]), {"title": "Raíces"})
        self.assertEqual(len(self.gh.commits), commits + 1)
        self.assertIn("mn/clases/Raíces/biseccion.yaml", self.gh.files)
        self.assertNotIn("mn/clases/unidad-01/biseccion.yaml", self.gh.files)
        deck = Deck.objects.get(title="Regula falsi")
        self.assertEqual(deck.github_path, "mn/clases/Raíces/regula.yaml")
        self.assertFalse(sync.pull(self.link).changed)          # ya estaba al día

    def test_cambiar_de_seccion_mueve_el_archivo(self):
        deck = Deck.objects.get(title="Suelta")
        unit2 = Section.objects.get(github_folder="unidad-02")
        self.client.force_login(self.teacher)
        self.client.post(reverse("deck_edit", args=[self.course.pk, deck.pk]),
                         {"source": SMALL.format(title="Suelta"), "section": unit2.pk})
        self.assertIn("mn/clases/unidad-02/suelta.yaml", self.gh.files)
        self.assertNotIn("mn/clases/suelta.yaml", self.gh.files)
        deck.refresh_from_db()
        self.assertEqual(deck.section, unit2)

    def test_eliminar_seccion_vacia_borra_el_marcador(self):
        unit10 = Section.objects.get(github_folder="unidad-10")
        self.client.force_login(self.teacher)
        self.client.post(reverse("section_delete", args=[self.course.pk, unit10.pk]))
        self.assertNotIn("mn/clases/unidad-10/.policlase-seccion", self.gh.files)
        self.assertFalse(Section.objects.filter(pk=unit10.pk).exists())

    def test_no_se_elimina_una_seccion_con_presentaciones(self):
        unit1 = Section.objects.get(github_folder="unidad-01")
        self.client.force_login(self.teacher)
        self.client.post(reverse("section_delete", args=[self.course.pk, unit1.pk]))
        self.assertTrue(Section.objects.filter(pk=unit1.pk).exists())
