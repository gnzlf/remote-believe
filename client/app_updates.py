"""Public GitHub releases, mandatory version gate, verified per-user installer."""
import hashlib
import json
from pathlib import Path
import re
from urllib import request, error, parse

REPOSITORY = "gnzlf/remote-believe"
DOWNLOAD_PAGE = "https://github.com/" + REPOSITORY + "/releases/latest"
API_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
MANIFEST_URL = DOWNLOAD_PAGE + "/download/update.json"
MAX_INSTALLER = 512 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-explorer)?", str(value or ""))
    if not match:
        raise UpdateError("La version publicada no es valida.")
    return tuple(map(int, match.groups()))


def validate_release(release):
    if release.get("draft") or release.get("prerelease"):
        raise UpdateError("La release no es una version estable.")
    version(release.get("tag_name"))
    candidates = [asset for asset in release.get("assets", []) if asset.get("name") == "RemoteBelieveSetup.exe"
                  and asset.get("state") == "uploaded"]
    if len(candidates) != 1:
        raise UpdateError("La release debe incluir un RemoteBelieveSetup.exe completo.")
    asset = candidates[0]
    digest = str(asset.get("digest", ""))
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise UpdateError("GitHub no publico la huella SHA256 del instalador.")
    prefix = "https://github.com/" + REPOSITORY + "/releases/download/"
    if not str(asset.get("browser_download_url", "")).startswith(prefix):
        raise UpdateError("El instalador no pertenece al repositorio de Believe.")
    if not 0 < int(asset.get("size", 0)) <= MAX_INSTALLER:
        raise UpdateError("El tamano del instalador no es valido.")
    return {"tag": release["tag_name"], "url": asset["browser_download_url"],
            "sha256": digest[7:].lower(), "size": int(asset["size"])}


class GitHubUpdates:
    def __init__(self, current, directory, opener=None):
        self.current, self.directory = current, Path(directory)
        self.opener = opener or request.build_opener()
        self.etag, self.release = None, None

    def check(self):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "RemoteBelieve/" + self.current,
                   "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache"}
        if self.etag:
            headers["If-None-Match"] = self.etag
        try:
            with self.opener.open(request.Request(API_URL, headers=headers), timeout=12) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise UpdateError("La respuesta de actualizacion es demasiado grande.")
                release = validate_release(json.loads(raw.decode("utf-8")))
                self.etag = response.headers.get("ETag")
                self.release = release
        except error.HTTPError as exc:
            if exc.code in (403, 429):
                self.release = self._public_manifest()
                self.etag = None
            elif exc.code != 304 or not self.release:
                raise UpdateError("No se pudo comprobar la ultima version en GitHub ({}). Reintenta.".format(exc.code)) from exc
        except (OSError, ValueError) as exc:
            self.release = self._public_manifest()
            self.etag = None
        return version(self.release["tag"]) > version(self.current), dict(self.release)

    def _public_manifest(self):
        # Public release assets avoid the shared unauthenticated API quota on studio networks.
        try:
            with self.opener.open(request.Request(MANIFEST_URL, headers={"User-Agent": "RemoteBelieve/" + self.current,
                    "Cache-Control": "no-cache"}), timeout=12) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise UpdateError("El manifiesto de actualizacion es demasiado grande.")
                manifest = json.loads(raw.decode("utf-8-sig"))
            tag = manifest["tag"]
            version(tag)
            release = {"tag_name": tag, "draft": False, "prerelease": False, "assets": [{
                "name": "RemoteBelieveSetup.exe", "state": "uploaded", "size": manifest["size"],
                "digest": "sha256:" + manifest["sha256"], "browser_download_url": manifest["url"]}]}
            return validate_release(release)
        except Exception as exc:
            raise UpdateError("No se pudo verificar la ultima version en GitHub. Revisa Internet y reintenta.") from exc

    def download(self, release, progress=None):
        # Revalidate an in-memory release, including its fixed origin, before downloading.
        if not release["url"].startswith("https://github.com/" + REPOSITORY + "/releases/download/"):
            raise UpdateError("Origen del instalador no permitido.")
        version(release["tag"])
        folder = self.directory / release["tag"]
        folder.mkdir(parents=True, exist_ok=True)
        destination, temporary = folder / "RemoteBelieveSetup.exe", folder / "RemoteBelieveSetup.downloading"
        digest, received = hashlib.sha256(), 0
        try:
            with self.opener.open(request.Request(release["url"], headers={"User-Agent": "RemoteBelieve/" + self.current}), timeout=30) as response:
                if parse.urlsplit(response.geturl()).scheme != "https":
                    raise UpdateError("La descarga debe usar HTTPS.")
                with temporary.open("wb") as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > release["size"] or received > MAX_INSTALLER:
                            raise UpdateError("El instalador excede el tamano publicado.")
                        digest.update(chunk)
                        stream.write(chunk)
                        if progress:
                            progress(received, release["size"])
            if received != release["size"] or digest.hexdigest() != release["sha256"]:
                raise UpdateError("El instalador no coincide con la huella publicada. Reintenta la descarga.")
            temporary.replace(destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)
