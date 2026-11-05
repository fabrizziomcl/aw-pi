"""Cliente secuencial con curl_cffi: robots.txt, pausas, Retry-After y límites de tamaño.

Un desafío antibots (Anubis, Cloudflare) detiene el repositorio: no se intenta eludir.
"""

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from scielo_mvp.http import retry_after

LOG = logging.getLogger("universidades")
CHALLENGES = (
    "making sure you're not a bot",
    "just a moment...",
    "cf-chl",
    "challenge-platform",
    "verify you are human",
)


class Robots:
    def __init__(self, text, agent):
        groups, current, after_agent = [], None, False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not after_agent:
                    current = {"agents": [], "rules": [], "delay": None}
                    groups.append(current)
                current["agents"].append(value.lower())
                after_agent = True
                continue
            after_agent = False
            if current is None:
                continue
            if key in ("allow", "disallow") and value:
                current["rules"].append((key == "allow", value, compile_rule(value)))
            elif key == "crawl-delay":
                try:
                    current["delay"] = float(value)
                except ValueError:
                    pass
        token = agent.lower()
        mine = [g for g in groups if any(a != "*" and a in token for a in g["agents"])]
        selected = mine or [g for g in groups if "*" in g["agents"]]
        self.rules = [rule for group in selected for rule in group["rules"]]
        delays = [group["delay"] for group in selected if group["delay"] is not None]
        self.crawl_delay = max(delays) if delays else None

    @classmethod
    def allow_all(cls):
        return cls("", "*")

    def allowed(self, url):
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best = None
        for allow, pattern, regex in self.rules:
            if regex.match(target):
                key = (len(pattern), allow)
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]


def compile_rule(pattern):
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if char == "*" else re.escape(char) for char in body)
    return re.compile(regex + ("$" if anchored else ""))


class FetchError(RuntimeError):
    pass


class Blocked(FetchError):
    pass


class Disallowed(FetchError):
    pass


class BrowserUnavailable(FetchError):
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


class Browser:
    def __init__(self, cfg, client):
        self.cfg = cfg
        self.client = client
        self.driver = None

    def _start(self):
        from selenium import webdriver

        executable = None
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            path = shutil.which(name)
            if not path:
                continue
            try:
                if subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).returncode == 0:
                    executable = path
                    break
            except (OSError, subprocess.SubprocessError):
                continue
        if not executable:
            raise BrowserUnavailable(
                "No se encontró Chrome/Chromium ejecutable; instala Google Chrome o Chromium para repositorios renderizados"
            )
        options = webdriver.ChromeOptions()
        options.binary_location = executable
        if self.cfg.headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1280,900")
        options.add_argument("--lang=es-PE")
        # Required in containers where Chrome cannot create a sandbox or use /dev/shm.
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        try:
            self.driver = webdriver.Chrome(options=options)
        except Exception as exc:  # noqa: BLE001 - Selenium agrupa aquí fallos de instalación
            raise BrowserUnavailable(f"No se pudo iniciar Chrome con Selenium: {exc}") from exc
        self.driver.set_page_load_timeout(self.cfg.timeout_seconds)
        LOG.info("Chrome headless iniciado para fichas renderizadas")

    def get(self, url, wait_for, wait_also=""):
        from selenium.common.exceptions import TimeoutException, WebDriverException
        from selenium.webdriver.support.ui import WebDriverWait

        self.client.check(url)
        self.client.wait(origin_of(url))
        if self.driver is None:
            self._start()
        try:
            self.driver.get(url)
            status = error_page(self.driver.page_source)
            if status in (401, 403, 429):
                raise Blocked(f"Acceso restringido, HTTP {status}: {url}")
            if status:
                raise FetchError(f"HTTP {status}: {url}")
            WebDriverWait(self.driver, self.cfg.render_timeout_seconds).until(
                lambda d: d.find_elements(*locator(wait_for)) or challenge(d.page_source)
            )
            if wait_also:
                try:
                    WebDriverWait(self.driver, self.cfg.settle_seconds).until(
                        lambda d: d.find_elements(*locator(wait_also))
                    )
                except TimeoutException:
                    pass
        except TimeoutException as exc:
            raise FetchError(f"La ficha no terminó de cargar en {self.cfg.render_timeout_seconds}s: {url}") from exc
        except WebDriverException as exc:
            raise FetchError(f"Error de Selenium en {url}: {str(exc).splitlines()[0]}") from exc
        html = self.driver.page_source
        marker = challenge(html)
        if marker:
            raise Blocked(f"Desafío antibots ({marker!r}) en {url}; no se elude")
        return Page(self.driver.current_url, html.encode("utf-8"), None, {})

    def close(self):
        if self.driver is not None:
            self.driver.quit()
            self.driver = None


def locator(selector):
    from selenium.webdriver.common.by import By

    return (By.XPATH, selector[6:]) if selector.startswith("xpath:") else (By.CSS_SELECTOR, selector)
