"""Cliente secuencial: robots, pausas, Retry-After y límites de tamaño."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests


class FetchError(RuntimeError):
    pass


class Blocked(FetchError):
    pass


@dataclass
class Page:
    url: str
    content: bytes
    status: int
    headers: dict


class Client:
    def __init__(self, cfg):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": cfg.user_agent})
        self.robots = {}
        self.last_request = {}
        self.reported_robots = set()

    def close(self):
        self.session.close()

    def _wait(self, origin):
        parser = self.robots.get(origin)
        delay = self.cfg.delay_seconds
        if parser:
            delay = max(delay, parser.crawl_delay(self.cfg.user_agent) or parser.crawl_delay("*") or 0)
        remaining = delay - (time.monotonic() - self.last_request.get(origin, 0))
        if remaining > 0:
            time.sleep(remaining)
        self.last_request[origin] = time.monotonic()

    def _request(self, url):
        origin = origin_of(url)
        for attempt in range(self.cfg.retries + 1):
            self._wait(origin)
            try:
                with self.session.get(
                    url, timeout=self.cfg.timeout_seconds, allow_redirects=False, stream=True
                ) as response:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < self.cfg.retries:
                        wait = retry_after(response.headers.get("Retry-After"), 2 ** (attempt + 1))
                        # No ignorar una pausa larga indicada por el servidor.
                        if wait > 300:
                            raise FetchError(f"Servidor pide esperar {wait:.0f}s: {url}; reanude más tarde")
                        time.sleep(wait)
                        continue
                    content = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        content.extend(chunk)
                        if len(content) > self.cfg.max_response_mb * 1024 * 1024:
                            raise FetchError(f"Respuesta supera max_response_mb: {url}")
                    return Page(url, bytes(content), response.status_code, dict(response.headers))
            except requests.RequestException as exc:
                if attempt == self.cfg.retries:
                    raise FetchError(f"Error de red en {url}: {exc}") from exc
                time.sleep(2 ** (attempt + 1))
        raise FetchError(f"No fue posible recuperar {url}")

    def _check_robots(self, url):
        origin = origin_of(url)
        if origin not in self.robots:
            robot_url = origin + "/robots.txt"
            page = self._request(robot_url)
            for _ in range(5):
                if page.status not in (301, 302, 303, 307, 308):
                    break
                page = self._request(urljoin(page.url, header(page, "location")))
            parser = RobotFileParser(robot_url)
            if page.status in (404, 410):
                parser.parse([])
            elif page.status == 200 and not page.content.lstrip().lower().startswith(
                (b"<html", b"<!doctype")
            ):
                # RobotFileParser trata una línea vacía tras User-agent como fin
                # del grupo. SciELO publica esa separación antes de Disallow;
                # ignorar líneas vacías conserva la regla del grupo correcto.
                lines = page.content.decode("utf-8-sig", errors="replace").splitlines()
                parser.parse([line for line in lines if line.strip()])
            else:
                raise Blocked(f"No se pudo verificar robots.txt (HTTP {page.status}): {robot_url}")
            self.robots[origin] = parser
        if not self.robots[origin].can_fetch(self.cfg.user_agent, url):
            if self.cfg.robots_policy == "report":
                if origin not in self.reported_robots:
                    logging.getLogger("scielo").warning(
                        "robots.txt restringe este rastreador en %s; robots_policy=report registra la directiva y continúa las solicitudes públicas HTTP",
                        origin,
                    )
                    self.reported_robots.add(origin)
                return
            raise Blocked(f"robots.txt no permite este rastreador: {url}")

    def get(self, url):
        for _ in range(6):
            self._check_robots(url)
            page = self._request(url)
            if page.status in (301, 302, 303, 307, 308):
                location = header(page, "location")
                if not location:
                    raise FetchError(f"Redirección sin Location: {url}")
                url = urljoin(url, location)
                continue
            if page.status in (401, 403, 429):
                raise Blocked(f"Acceso restringido, HTTP {page.status}: {url}")
            if page.status != 200:
                raise FetchError(f"HTTP {page.status}: {url}")
            return page
        raise FetchError(f"Demasiadas redirecciones: {url}")


def header(page, name):
    return next((v for k, v in page.headers.items() if k.lower() == name.lower()), "")


def origin_of(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username:
        raise FetchError(f"URL HTTP(S) inválida: {url}")
    return f"{parts.scheme}://{parts.netloc}"


def retry_after(value, fallback):
    if value:
        try:
            return max(0, float(value))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return fallback
