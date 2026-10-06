"""Cliente mínimo de la API REST de GitHub: solo lo que la sincronización necesita.

Sin dependencias: urllib y JSON. Las pruebas lo reemplazan por un repositorio en memoria
(`apps.github.tests.FakeGitHub`) con la misma interfaz.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from django.utils.translation import gettext as _

API = "https://api.github.com"
TIMEOUT_S = 8


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Conflict(GitHubError):
    """El archivo cambió en GitHub desde que lo leímos (el sha no coincide)."""


class GitHubClient:
    def __init__(self, token: str):
        self.token = token

    # ---------------------------------------------------------------- transporte

    def _call(self, method: str, path: str, body: dict | None = None) -> dict | list:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "policlase",
            **({"Content-Type": "application/json"} if data else {}),
        })
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read() or b"{}").get("message", "")
            except ValueError:
                pass
            if exc.code in (409, 422) and method in ("PUT", "DELETE"):
                raise Conflict(_("El archivo cambió en GitHub."), exc.code) from None
            if exc.code == 401:
                raise GitHubError(_("GitHub rechazó el token (¿venció o fue revocado?)."), 401) from None
            if exc.code in (403, 404):
                raise GitHubError(_("Sin acceso: revise el nombre del repositorio, la rama y los "
                                    "permisos del token (Contents: lectura y escritura)."), exc.code) from None
            raise GitHubError(f"GitHub {exc.code}: {detail}", exc.code) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GitHubError(_("No se pudo conectar con GitHub: %(error)s") % {"error": exc}) from None
        return json.loads(raw) if raw else {}

    @staticmethod
    def _q(text: str) -> str:
        return urllib.parse.quote(text, safe="/")

    # -------------------------------------------------------------------- lectura

    def login(self) -> str:
        return self._call("GET", "/user")["login"]

    def check_repo(self, repo: str, branch: str) -> None:
        """Falla si el token no puede leer y escribir en la rama del repositorio."""
        info = self._call("GET", f"/repos/{self._q(repo)}")
        if not (info.get("permissions") or {}).get("push"):
            raise GitHubError(_("El token puede leer el repositorio pero no escribir en él."))
        self._call("GET", f"/repos/{self._q(repo)}/branches/{self._q(branch)}")

    def tree(self, repo: str, branch: str) -> dict[str, str]:
        """{ruta: blob sha} de todos los archivos de la rama."""
        data = self._call("GET", f"/repos/{self._q(repo)}/git/trees/{self._q(branch)}?recursive=1")
        return {e["path"]: e["sha"] for e in data.get("tree", []) if e.get("type") == "blob"}

    def read(self, repo: str, sha: str) -> str:
        data = self._call("GET", f"/repos/{self._q(repo)}/git/blobs/{sha}")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ escritura

    def write(self, repo: str, branch: str, path: str, text: str, message: str, sha: str = "") -> str:
        """Crea o actualiza un archivo; devuelve el sha nuevo. `sha` vacío = archivo nuevo."""
        body = {"message": message, "branch": branch,
                "content": base64.b64encode(text.encode("utf-8")).decode()}
        if sha:
            body["sha"] = sha
        data = self._call("PUT", f"/repos/{self._q(repo)}/contents/{self._q(path)}", body)
        return data["content"]["sha"]

    def delete(self, repo: str, branch: str, path: str, message: str, sha: str) -> None:
        self._call("DELETE", f"/repos/{self._q(repo)}/contents/{self._q(path)}",
                   {"message": message, "branch": branch, "sha": sha})
