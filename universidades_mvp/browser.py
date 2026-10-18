"""Renderizado con Selenium (Chrome headless) para interfaces que arman la ficha con JavaScript.

Comparte robots.txt y pausas con el cliente HTTP; solo se inicia si algún repositorio lo necesita.
"""

import logging

from .http import Blocked, FetchError, Page, challenge, error_page, origin_of

LOG = logging.getLogger("universidades")


class Browser:
    def __init__(self, cfg, client):
        self.cfg = cfg
        self.client = client
        self.driver = None

    def _start(self):
        from selenium import webdriver

        options = webdriver.ChromeOptions()
        if self.cfg.headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1280,900")
        options.add_argument("--lang=es-PE")
        try:
            self.driver = webdriver.Chrome(options=options)
        except Exception as exc:  # noqa: BLE001 - Selenium agrupa aquí fallos de instalación
            raise FetchError(f"No se pudo iniciar Chrome con Selenium: {exc}") from exc
        self.driver.set_page_load_timeout(self.cfg.timeout_seconds)
        LOG.info("Chrome headless iniciado para fichas renderizadas")

    def get(self, url, wait_for, wait_also=""):
        """wait_for es obligatorio; wait_also (p. ej., los archivos) se espera poco y es opcional."""
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
                    pass  # Tesis embargadas o sin archivos: no es un error.
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
