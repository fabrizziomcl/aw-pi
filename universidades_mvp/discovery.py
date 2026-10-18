"""Descubrimiento de fichas sin buscador ni API: sitemaps XML (DSpace 7+), htmlmap e índice paginado (DSpace 6)."""

import html
import logging
import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from scielo_mvp.parsers import ParseError

LOG = logging.getLogger("universidades")
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def normalize(url, repo, rules=()):
    for pattern, replacement in rules:
        url = re.sub(pattern, replacement, url)
    parts, base = urlsplit(url), urlsplit(repo.base_url)
    # Algunos sitemaps declaran http:// para el mismo host; se unifica con base_url.
    if parts.hostname == base.hostname and parts.scheme != base.scheme:
        url = urlunsplit((base.scheme, parts.netloc, parts.path, parts.query, ""))
    return url


def sitemap_items(repo, client):
    pattern = re.compile(repo.item_pattern)
    pending, seen, items = [repo.base_url + repo.sitemap], set(), []
    while pending:
        url = normalize(pending.pop(0), repo, repo.sitemap_rewrites)
        if url in seen:
            continue
        seen.add(url)
        LOG.info("%s: sitemap %s", repo.key, url)
        text = client.get(url).content.decode("utf-8", "replace")
        locs = [html.unescape(loc) for loc in LOC.findall(text)]
        if "<sitemapindex" in text:
            pending.extend(locs)
            continue
        if "<urlset" not in text:
            raise ParseError(f"{url} no es un sitemap XML; revise sitemap o sitemap_rewrites")
        found = [normalize(loc, repo, repo.item_rewrites) for loc in locs]
        items.extend(loc for loc in found if pattern.search(loc))
        LOG.info("%s: %s URL, %s fichas", repo.key, len(locs), sum(bool(pattern.search(loc)) for loc in found))
    return list(dict.fromkeys(items))


def htmlmap_items(repo, client):
    pattern = re.compile(repo.item_pattern)
    page = client.get(repo.base_url + repo.sitemap)
    soup = BeautifulSoup(page.content, "html.parser")
    maps = [urljoin(page.url, a["href"]) for a in soup.select("a[href]") if "htmlmap?map=" in a["href"]]
    items = []
    for url in list(dict.fromkeys(maps)) or [page.url]:
        url = normalize(url, repo, repo.sitemap_rewrites)
        LOG.info("%s: htmlmap %s", repo.key, url)
        listing = client.get(url) if url != page.url else page
        for anchor in BeautifulSoup(listing.content, "html.parser").select("a[href]"):
            target = normalize(urljoin(listing.url, anchor["href"]), repo, repo.item_rewrites)
            if pattern.search(target):
                items.append(target)
    return list(dict.fromkeys(items))


def browse_items(repo, client):
    """Índice paginado de DSpace 6 (`/browse?...&offset=N`): sigue el enlace al offset siguiente."""
    pattern = re.compile(repo.item_pattern)
    url, seen, items = repo.base_url + repo.sitemap, set(), []
    while url and url not in seen:
        seen.add(url)
        page = client.get(url)
        offset = int(parse_qs(urlsplit(page.url).query).get("offset", ["0"])[0])
        following = None
        for anchor in BeautifulSoup(page.content, "html.parser").select("a[href]"):
            target = normalize(urljoin(page.url, anchor["href"]), repo, repo.item_rewrites)
            if pattern.search(target):
                items.append(target)
                continue
            value = parse_qs(urlsplit(target).query).get("offset", [""])[0]
            if value.isdigit() and int(value) > offset and (following is None or int(value) < following[0]):
                following = (int(value), target)
        if len(seen) % 20 == 0:
            LOG.info("%s: índice, %s páginas y %s fichas", repo.key, len(seen), len(set(items)))
        url = following[1] if following else None
    return list(dict.fromkeys(items))


def discover(repo, client):
    if repo.discovery == "htmlmap":
        return htmlmap_items(repo, client)
    if repo.discovery == "browse":
        return browse_items(repo, client)
    return sitemap_items(repo, client)
