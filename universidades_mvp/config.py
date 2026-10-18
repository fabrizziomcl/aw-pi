"""Configuración validada; el perfil recomendado es config.universidades.json."""

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

DISCOVERY = ("sitemap", "htmlmap", "browse")
PARSERS = ("dspace", "mui")


@dataclass
class Repo:
    """Diferencias de un repositorio respecto del flujo común."""

    key: str
    name: str
    base_url: str
    discovery: str = "sitemap"
    sitemap: str = "/sitemap_index.xml"
    # Pares [regex, reemplazo] aplicados a las URL de sub-sitemaps y de fichas.
    sitemap_rewrites: list = field(default_factory=list)
    item_rewrites: list = field(default_factory=list)
    item_pattern: str = r"/(items|entities/publication)/[0-9a-f-]{36}/?$"
    item_suffix: str = ""
    render: bool = False
    wait_for: str = "meta[name='citation_title']"
    # Espera breve y opcional (p. ej., enlaces a archivos que cargan después de la ficha).
    wait_also: str = "meta[name='citation_pdf_url'], a[href*='/bitstreams/']"
    parser: str = "dspace"
    enabled: bool = True

    @property
    def origin(self):
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def discovery_settings(self):
        """Lo que define la muestra: si cambia, hay que usar otra carpeta."""
        return {
            key: getattr(self, key)
            for key in ("base_url", "discovery", "sitemap", "sitemap_rewrites", "item_rewrites", "item_pattern")
        }


@dataclass
class Config:
    data_dir: str = "data/universidades"
    languages: list[str] = field(default_factory=lambda: ["es"])
    thesis_only: bool = True
    sample_seed: str = "aw-2026"
    delay_seconds: float = 2.0
    timeout_seconds: float = 40
    render_timeout_seconds: float = 30
    settle_seconds: float = 5
    retries: int = 2
    max_response_mb: float = 40
    impersonate: str = "chrome"
    robots_agent: str = "UniversidadesMVP"
    batch_size: int = 25
    max_consecutive_errors: int = 10
    headless: bool = True
    repos: list[Repo] = field(default_factory=list)

    @classmethod
    def load(cls, path):
        values = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict):
            raise ValueError("La configuración debe ser un objeto JSON")
        unknown = set(values) - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError("Parámetros desconocidos: " + ", ".join(sorted(unknown)))
        repos = values.pop("repos", None)
        if not isinstance(repos, list) or not repos:
            raise ValueError("repos debe ser una lista no vacía")
        if "languages" in values:
            if values["languages"] in (None, ""):
                values["languages"] = []
            if not isinstance(values["languages"], list):
                raise ValueError("languages debe ser una lista, [] o null")
            values["languages"] = [str(v).strip().lower() for v in values["languages"] if str(v).strip()]
        cfg = cls(**values, repos=[load_repo(item) for item in repos])
        for key in ("data_dir", "sample_seed", "impersonate", "robots_agent"):
            if not isinstance(getattr(cfg, key), str):
                raise ValueError(f"{key} debe ser texto")
        if not cfg.data_dir.strip() or not cfg.robots_agent.strip():
            raise ValueError("data_dir y robots_agent no pueden estar vacíos")
        for key in ("thesis_only", "headless"):
            if not isinstance(getattr(cfg, key), bool):
                raise ValueError(f"{key} debe ser true o false")
        for key in ("retries", "batch_size", "max_consecutive_errors"):
            if type(getattr(cfg, key)) is not int:
                raise ValueError(f"{key} debe ser un entero")
        for key in ("delay_seconds", "timeout_seconds", "render_timeout_seconds", "settle_seconds", "max_response_mb"):
            value = getattr(cfg, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} debe ser un número finito")
        if any(not re.fullmatch(r"[a-z]{2}", value) for value in cfg.languages):
            raise ValueError("languages requiere códigos de dos letras, como es o en")
        if cfg.delay_seconds < 1:
            raise ValueError("delay_seconds debe ser al menos 1 para no saturar los repositorios")
        if min(cfg.retries, cfg.max_consecutive_errors) < 0 or cfg.batch_size < 1:
            raise ValueError("retries y max_consecutive_errors no pueden ser negativos; batch_size debe ser positivo")
        if min(cfg.timeout_seconds, cfg.render_timeout_seconds, cfg.max_response_mb) <= 0 or cfg.settle_seconds < 0:
            raise ValueError("Los tiempos de espera y max_response_mb deben ser positivos")
        keys = [repo.key for repo in cfg.repos]
        if len(keys) != len(set(keys)):
            raise ValueError("Hay claves de repositorio repetidas")
        return cfg

    def selected(self, keys=None):
        if not keys:
            return [repo for repo in self.repos if repo.enabled]
        known = {repo.key: repo for repo in self.repos}
        missing = [key for key in keys if key not in known]
        if missing:
            raise ValueError("Repositorios desconocidos: " + ", ".join(missing))
        return [known[key] for key in keys]

    def as_dict(self):
        return asdict(self)


def load_repo(values):
    if not isinstance(values, dict):
        raise ValueError("Cada repositorio debe ser un objeto JSON")
    unknown = set(values) - {item.name for item in fields(Repo)}
    if unknown:
        raise ValueError(f"{values.get('key', '?')}: parámetros desconocidos: " + ", ".join(sorted(unknown)))
    try:
        repo = Repo(**values)
    except TypeError as exc:
        raise ValueError(f"Repositorio incompleto: {exc}") from exc
    if not re.fullmatch(r"[a-z0-9_]+", str(repo.key)):
        raise ValueError(f"{repo.key}: key solo admite minúsculas, dígitos y _")
    parts = urlsplit(str(repo.base_url))
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        raise ValueError(f"{repo.key}: base_url debe ser una URL HTTP(S) pública")
    repo.base_url = repo.base_url.rstrip("/")
    if repo.discovery not in DISCOVERY:
        raise ValueError(f"{repo.key}: discovery debe ser " + " o ".join(DISCOVERY))
    if repo.parser not in PARSERS:
        raise ValueError(f"{repo.key}: parser debe ser " + " o ".join(PARSERS))
    for key in ("render", "enabled"):
        if not isinstance(getattr(repo, key), bool):
            raise ValueError(f"{repo.key}: {key} debe ser true o false")
    for key in ("sitemap_rewrites", "item_rewrites"):
        rules = getattr(repo, key)
        if not isinstance(rules, list) or any(
            not isinstance(rule, list) or len(rule) != 2 or not all(isinstance(v, str) for v in rule)
            for rule in rules
        ):
            raise ValueError(f"{repo.key}: {key} debe ser una lista de pares [regex, reemplazo]")
        for pattern, _ in rules:
            compile_regex(repo.key, key, pattern)
    compile_regex(repo.key, "item_pattern", repo.item_pattern)
    if not repo.sitemap.startswith("/"):
        raise ValueError(f"{repo.key}: sitemap debe ser una ruta que empiece con /")
    return repo


def compile_regex(key, name, pattern):
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{key}: {name} no es una expresión regular válida: {exc}") from exc
