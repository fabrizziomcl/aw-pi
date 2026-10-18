"""Cliente secuencial con curl_cffi: robots.txt, pausas, Retry-After y límites de tamaño.

Un desafío antibots (Anubis, Cloudflare) detiene el repositorio: no se intenta eludir.
"""

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from scielo_mvp.http import retry_after

from .robots import Robots

LOG = logging.getLogger("universidades")
CHALLENGES = (
    "making sure you're not a bot",
    "just a moment...",
    "cf-chl",
    "challenge-platform",
    "verify you are human",
)


class FetchError(RuntimeError):
    pass


class Blocked(FetchError):
    pass


class Disallowed(FetchError):
    pass


@dataclass
class Page:
    url: str
    content: bytes
    status: int | None
    headers: dict


def origin_of(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username:
        raise FetchError(f"URL HTTP(S) inválida: {url}")
    return f"{parts.scheme}://{parts.netloc}"


def challenge(content):
    head = (content[:30000] if isinstance(content, str) else content[:30000].decode("utf-8", "replace")).lower()
    return next((marker for marker in CHALLENGES if marker in head), None)


def error_page(content):
    """Código de una página de error del servidor (Selenium no expone el estado HTTP)."""
    head = content[:3000] if isinstance(content, str) else content[:3000].decode("utf-8", "replace")
    match = re.search(r"<title>\s*(4\d\d|5\d\d)\b[^<]*</title>", head, re.I)
    if match:
        return int(match[1])
    return 429 if re.search(r"too many requests", head, re.I) else None


class Client:
    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session(impersonate=cfg.impersonate or None)
        self.robots = {}
        self.last_request = {}

    def close(self):
        self.session.close()

    def delay(self, origin):
        robots = self.robots.get(origin)
        return max(self.cfg.delay_seconds, (robots.crawl_delay or 0) if robots else 0)

    def wait(self, origin):
        remaining = self.delay(origin) - (time.monotonic() - self.last_request.get(origin, -1e9))
        if remaining > 0:
            time.sleep(remaining)
        self.last_request[origin] = time.monotonic()

    def _request(self, url):
        origin = origin_of(url)
        for attempt in range(self.cfg.retries + 1):
            self.wait(origin)
            try:
                response = self.session.get(
                    url, timeout=self.cfg.timeout_seconds, allow_redirects=False, stream=True
                )
                try:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < self.cfg.retries:
                        wait = retry_after(response.headers.get("Retry-After"), 2 ** (attempt + 1))
                        if wait > 300:
                            raise FetchError(f"Servidor pide esperar {wait:.0f}s: {url}; reanude más tarde")
                        time.sleep(wait)
                        continue
                    content = bytearray()
                    for chunk in response.iter_content():
                        content.extend(chunk)
                        if len(content) > self.cfg.max_response_mb * 1024 * 1024:
                            raise FetchError(f"Respuesta supera max_response_mb: {url}")
                    return Page(url, bytes(content), response.status_code, dict(response.headers))
                finally:
                    response.close()
            except RequestException as exc:
                if attempt == self.cfg.retries:
                    raise FetchError(f"Error de red en {url}: {exc}") from exc
                time.sleep(2 ** (attempt + 1))
        raise FetchError(f"No fue posible recuperar {url}")

    def robots_for(self, origin):
        if origin not in self.robots:
            url = origin + "/robots.txt"
            page = self._request(url)
            for _ in range(5):
                if page.status not in (301, 302, 303, 307, 308):
                    break
                page = self._request(urljoin(page.url, header(page, "location")))
            text = page.content.decode("utf-8-sig", errors="replace")
            if page.status == 200 and not text.lstrip().lower().startswith(("<html", "<!doctype")):
                self.robots[origin] = Robots(text, self.cfg.robots_agent)
            elif page.status == 200 or 400 <= page.status < 500 and page.status != 429:
                # Sin robots.txt (o una página HTML en su lugar): no hay restricciones declaradas.
                self.robots[origin] = Robots.allow_all()
            else:
                raise Blocked(f"No se pudo verificar robots.txt (HTTP {page.status}): {url}")
            delay = self.robots[origin].crawl_delay
            if delay:
                LOG.info("robots.txt de %s pide Crawl-delay %ss", origin, delay)
        return self.robots[origin]

    def check(self, url):
        if not self.robots_for(origin_of(url)).allowed(url):
            raise Disallowed(f"robots.txt no permite esta URL: {url}")

    def get(self, url):
        for _ in range(6):
            self.check(url)
            page = self._request(url)
            if page.status in (301, 302, 303, 307, 308):
                location = header(page, "location")
                if not location:
                    raise FetchError(f"Redirección sin Location: {url}")
                url = urljoin(url, location)
                continue
            if page.status in (401, 403, 429):
                raise Blocked(f"Acceso restringido, HTTP {page.status}: {url}")
            marker = challenge(page.content)
            if marker:
                raise Blocked(f"Desafío antibots ({marker!r}) en {url}; no se elude")
            if page.status != 200:
                raise FetchError(f"HTTP {page.status}: {url}")
            return page
        raise FetchError(f"Demasiadas redirecciones: {url}")


def header(page, name):
    return next((v for k, v in page.headers.items() if k.lower() == name.lower()), "")
