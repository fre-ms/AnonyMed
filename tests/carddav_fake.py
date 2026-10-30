"""An in-process CardDAV server with canned address books for the tests.

Supports PROPFIND on the address-book home and REPORT addressbook-query with a
text-match filter on FN/N, exactly as the client uses them.  Records every
request so that tests can check methods and headers.
"""

from __future__ import annotations

import base64
import re
import threading
from dataclasses import dataclass, field
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

_TEXT_MATCH = re.compile(r"<c:text-match[^>]*>([^<]*)</c:text-match>")


@dataclass
class Anfrage:
    methode: str
    pfad: str
    headers: dict[str, str]
    body: str


@dataclass
class FakeCardDAV:
    benutzer: str = "test"
    passwort: str = "app-pw-1234"
    buecher: dict[str, tuple[str, list[str]]] = field(default_factory=dict)  # uri -> (name, cards)
    anfragen: list[Anfrage] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None

    @property
    def home(self) -> str:
        return f"/remote.php/dav/addressbooks/users/{self.benutzer}/"

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> FakeCardDAV:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:  # silence
                pass

            def _body(self) -> str:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n).decode("utf-8") if n else ""

            def _antwort(self, status: int, text: str = "") -> None:
                daten = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(daten)))
                if status == 401:
                    self.send_header("WWW-Authenticate", 'Basic realm="fake"')
                self.end_headers()
                self.wfile.write(daten)

            def _auth_ok(self) -> bool:
                erwartet = base64.b64encode(f"{fake.benutzer}:{fake.passwort}".encode()).decode()
                return self.headers.get("Authorization") == f"Basic {erwartet}"

            def _merke(self, body: str) -> None:
                fake.anfragen.append(Anfrage(self.command, self.path, dict(self.headers), body))

            def do_PROPFIND(self) -> None:  # noqa: N802
                body = self._body()
                self._merke(body)
                if not self._auth_ok():
                    return self._antwort(401)
                if self.path != fake.home:
                    return self._antwort(404)
                teile = [
                    f"<d:response><d:href>{fake.home}</d:href><d:propstat><d:prop>"
                    "<d:resourcetype><d:collection/></d:resourcetype></d:prop>"
                    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                ]
                for uri, (name, _karten) in fake.buecher.items():
                    teile.append(
                        f"<d:response><d:href>{fake.home}{uri}/</d:href><d:propstat><d:prop>"
                        f"<d:displayname>{escape(name)}</d:displayname>"
                        "<d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>"
                        "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                    )
                return self._antwort(207, _multistatus("".join(teile)))

            def do_REPORT(self) -> None:  # noqa: N802
                body = self._body()
                self._merke(body)
                if not self._auth_ok():
                    return self._antwort(401)
                uri = self.path.rstrip("/").rsplit("/", 1)[-1]
                if self.path != f"{fake.home}{uri}/" or uri not in fake.buecher:
                    return self._antwort(404)
                m = _TEXT_MATCH.search(body)
                begriff = unescape(m.group(1)).casefold() if m else ""
                teile = []
                for i, karte in enumerate(fake.buecher[uri][1]):
                    kopf = "\n".join(z for z in karte.splitlines() if z.startswith(("FN:", "N:")))
                    if begriff and begriff not in kopf.casefold():
                        continue
                    teile.append(
                        f"<d:response><d:href>{self.path}karte{i}.vcf</d:href><d:propstat><d:prop>"
                        f'<d:getetag>"etag{i}"</d:getetag>'
                        f"<c:address-data>{escape(karte)}</c:address-data>"
                        "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                    )
                return self._antwort(207, _multistatus("".join(teile)))

            def do_GET(self) -> None:  # noqa: N802
                self._merke("")
                self._antwort(404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _multistatus(inhalt: str) -> str:
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"'
        f' xmlns:c="urn:ietf:params:xml:ns:carddav">{inhalt}</d:multistatus>'
    )
