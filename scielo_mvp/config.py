"""Configuración validada; el perfil recomendado es config.peru-catalog.json."""

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import urlencode, urlsplit

SEARCH_URL = "https://search.scielo.org/"


@dataclass
class Config:
    query: str = ""
    languages: list[str] = field(default_factory=lambda: ["es"])
    collections: list[str] = field(default_factory=lambda: ["per"])
    years: list[str] = field(default_factory=list)
    scielo_areas: list[str] = field(default_factory=list)
    wos_areas: list[str] = field(
        default_factory=lambda: [
            "engineering",
            "sciences",
            "computer",
            "systems",
            "information",
            "technolog*",
        ]
    )
    document_types: list[str] = field(default_factory=list)
    page_size: int = 15
    sort: str = "da asc"
    max_pages: int = 3
    max_articles: int = 20
    delay_seconds: float = 3.0
    timeout_seconds: float = 40
    retries: int = 2
    max_response_mb: float = 40
    user_agent: str = "SciELO-MVP/0.1 (small research crawler)"
    data_dir: str = "data"
    discovery: str = "search"
    catalog_url: str = ""
    catalog_journals: list[str] = field(default_factory=list)
    robots_policy: str = "respect"

    @classmethod
    def load(cls, path):
        values = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict):
            raise ValueError("La configuración debe ser un objeto JSON")
        unknown = set(values) - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError("Parámetros desconocidos: " + ", ".join(sorted(unknown)))
        for key in (
            "languages",
            "collections",
            "years",
            "scielo_areas",
            "wos_areas",
            "document_types",
            "catalog_journals",
        ):
            if key in values and values[key] in (None, ""):
                values[key] = []
            if key in values and not isinstance(values[key], list):
                raise ValueError(f"{key} debe ser una lista, [] o null")
            if key in values and any(
                isinstance(v, bool) or not isinstance(v, (str, int)) for v in values[key]
            ):
                raise ValueError(f"{key} contiene valores no válidos")
            if key in values:
                values[key] = list(dict.fromkeys(str(v).strip() for v in values[key] if str(v).strip()))
        cfg = cls(**values)
        for key in ("query", "sort", "user_agent", "data_dir", "discovery", "catalog_url", "robots_policy"):
            if not isinstance(getattr(cfg, key), str):
                raise ValueError(f"{key} debe ser texto")
        if not cfg.data_dir.strip() or not cfg.user_agent.strip():
            raise ValueError("data_dir y user_agent no pueden estar vacíos")
        for key in ("page_size", "max_pages", "max_articles", "retries"):
            if type(getattr(cfg, key)) is not int:
                raise ValueError(f"{key} debe ser un entero")
        for key in ("delay_seconds", "timeout_seconds", "max_response_mb"):
            value = getattr(cfg, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} debe ser un número finito")
        if any(not re.fullmatch(r"[a-z]{2,3}", value) for value in cfg.languages):
            raise ValueError("languages requiere códigos como es, en o pt")
        if any(not re.fullmatch(r"\d{4}", value) for value in cfg.years):
            raise ValueError("years requiere años de cuatro dígitos")
        if any(not re.fullmatch(r"\d{4}-[\dXx]{4}", value) for value in cfg.catalog_journals):
            raise ValueError("catalog_journals requiere ISSN con formato 1234-5678")
        if cfg.discovery not in ("search", "catalog"):
            raise ValueError("discovery debe ser search o catalog")
        if cfg.robots_policy not in ("respect", "report"):
            raise ValueError("robots_policy debe ser respect o report")
        if cfg.discovery == "catalog":
            if not cfg.catalog_url:
                raise ValueError("La ruta catalog requiere catalog_url")
            parts = urlsplit(cfg.catalog_url)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
                raise ValueError("catalog_url debe ser una URL HTTP(S) pública")
            if cfg.query or cfg.wos_areas or cfg.scielo_areas or cfg.document_types:
                raise ValueError(
                    "El catálogo no aplica consultas ni facetas WoS/SciELO/tipo; use catalog_journals explícitos y vacíe esos filtros"
                )
            known_collection = {"www.scielo.org.pe": "per", "www.scielo.org.za": "sza"}.get(
                urlsplit(cfg.catalog_url).hostname
            )
            if cfg.collections and cfg.collections != [known_collection]:
                raise ValueError("collections no coincide con el host del catálogo; use su código o []")
            if not isinstance(cfg.catalog_journals, list):
                raise ValueError("catalog_journals debe ser una lista de ISSN o []")
        if not 1 <= cfg.page_size <= 100:
            raise ValueError("page_size debe estar entre 1 y 100")
        if min(cfg.max_pages, cfg.max_articles, cfg.retries, cfg.delay_seconds) < 0:
            raise ValueError("Los límites, retries y delay_seconds no pueden ser negativos")
        if cfg.timeout_seconds <= 0 or cfg.max_response_mb <= 0:
            raise ValueError("timeout_seconds y max_response_mb deben ser positivos")
        return cfg

    def as_dict(self):
        return asdict(self)


def term(value):
    value = str(value).strip()
    # Wildcards explícitos: technolog* no se convierte en una frase literal.
    if value.endswith("*") and value[:-1].isalnum():
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_query(cfg):
    clauses = [f"({cfg.query.strip()})"] if cfg.query.strip() else []
    for name, values in [
        ("la", cfg.languages),
        ("in", cfg.collections),
        ("publication_year", cfg.years),
        ("subject_area", cfg.scielo_areas),
        ("wok_subject_categories", cfg.wos_areas),
        ("type", cfg.document_types),
    ]:
        cleaned = [term(v) for v in values if str(v).strip()]
        if cleaned:
            clauses.append(f"{name}:(" + " OR ".join(cleaned) + ")")
    return " AND ".join(clauses)


def search_url(cfg, page=1):
    if page < 1:
        raise ValueError("page empieza en 1")
    return (
        SEARCH_URL
        + "?"
        + urlencode(
            {
                "lang": "en",
                "count": cfg.page_size,
                "from": (page - 1) * cfg.page_size,
                "output": "site",
                "sort": cfg.sort,
                "format": "summary",
                "fb": "",
                "page": page,
                "q": build_query(cfg),
            }
        )
    )
