# Repositorios universitarios scrapeables

Estos 12 repositorios peruanos pueden extraerse con **curl_cffi, BeautifulSoup y Selenium únicamente**, sin eludir medidas antibots ni contradecir su robots.txt.

Se evaluaron el 8 de octubre de 2026. En cada sitio se revisaron la portada, el robots.txt, el sitemap y al menos una ficha real, con pausas de 1,5 s entre solicitudes. La accesibilidad puede cambiar: conviene volver a comprobarla antes de una extracción completa.

## Links
Exp
1. https://repositorio.upch.edu.pe/home
2. https://repositorio.unfv.edu.pe/home
3. https://repositorio.unap.edu.pe/home
4. https://repositorio.unica.edu.pe/home
5. https://repositorio.lamolina.edu.pe/home
6. https://repositorio.esan.edu.pe/home
7. https://repositorio.upn.edu.pe/
8. https://repositorio.uncp.edu.pe/home
9. https://repositorio.unc.edu.pe/home
10. https://repositorio.unheval.edu.pe/

## Detalle por repositorio

Se dice que un sitio tiene **SSR** cuando el servidor entrega el HTML con el contenido. **CSR** significa que el servidor envía una página vacía y el contenido lo arma el navegador con JavaScript. En DSpace, las etiquetas `citation_*` del HTML traen los metadatos de cada ficha: título, autor, abstract y PDF.

### Con curl_cffi + BeautifulSoup (SSR)

| # | Universidad | Plataforma | Fichas en sitemap | Descubrimiento | Notas |
|---|---|---|---|---|---|
| 1 | UPCH | DSpace 8.1 | 14.036 | `/sitemap_index.xml` | Incluye `citation_*`. robots.txt pide 10 s entre solicitudes |
| 2 | UNFV | DSpace 9.2 | 11.706 | `/sitemap_index.xml` | Incluye `citation_*` |
| 3 | UNMSM | DSpace 6.3 | 1.615 | `/htmlmap` | Trae título, abstract, autor y enlace al PDF. Es un repositorio pequeño (sobre todo tesis históricas): el índice por fecha da 1.581 fichas, menos que `/htmlmap`. robots.txt solo prohíbe `/discover` y `/search-filter` |
| 4 | UNAP | DSpace 7.6.2 | 18.491 | `/sitemap_index.xml` | Incluye `citation_*` y PDF |
| 5 | UNICA | DSpace 9.2 | 5.307 | `/sitemap_index.xml` | Incluye `citation_*` y PDF |

### Con Selenium para las fichas + curl_cffi para el sitemap (CSR)

| # | Universidad | Plataforma | Fichas en sitemap | Descubrimiento | Notas |
|---|---|---|---|---|---|
| 6 | La Molina | DSpace 7+ | 6.411 | `/sitemap_index.xml` | Con Selenium, unos 3 s por ficha, con `citation_*` |
| 7 | UPC | DSpace 7+ | 71.596 | `/sitemap_index.xml` | ⚠️ Respondió **HTTP 429** tras unas pocas fichas renderizadas, aun respetando 10 s entre fichas: cada ficha renderizada consulta `/server/api`, que su robots.txt prohíbe. Deshabilitado por defecto en `universidades_mvp`. robots.txt prohíbe `/browse`, `/search`, `/full`, la API y OAI |
| 8 | ESAN | DSpace 7+ | 4.011 | `/sitemap_index.xml` | El sitemap y los enlaces a PDF apuntan por error a `localhost:4000`: reemplazar por el dominio real |
| 9 | UPN | Interfaz propia sobre DSpace | 37.202 | `/backend/sitemaps/sitemap_index.xml` | Las URL `/items/<uuid>` del sitemap redirigen a la portada: usar `/item/<uuid>`. Incluye `citation_*` |
| 10 | UNCP | DSpace 7+ | 14.537 | `/server/sitemaps/sitemap0.xml` | El sitemap que declara robots.txt devuelve la página vacía. Con Selenium, unos 1,3 s por ficha, con `citation_*` y PDF. robots.txt: `Allow: /` |
| 11 | UNC | DSpace 7+ | 10.853 | `/sitemap_index.xml` | Con Selenium, unos 1,3 s por ficha, con `citation_*` y PDF |
| 12 | UNHEVAL | Interfaz propia "dspace-react" (Intelego) | 14.021 | `/backend/sitemaps/sitemap0.xml` | Usar `/item/<uuid>` en lugar de `/items/<uuid>`. No hay `citation_*`: extraer título, autores, fecha y resumen del texto visible (rótulos "Autor(es)", "Fecha de publicación" y "Resumen"). PDF en `/backend/api/core/bitstreams/<uuid>/content` |

## Observaciones

- **El sitemap como punto de partida:** permite listar fichas sin usar `/search`, que robots.txt suele prohibir. Los 12 repositorios suman unas **210.000 fichas**. Incluyen todo tipo de documento (artículos, sílabos, informes), no solo tesis, así que hay que filtrar por tipo.
- **Selenium** solo es necesario para renderizar las fichas de los 7 sitios CSR. Conviene esperar un elemento concreto, no una pausa fija; en UPN y UNHEVAL hay que esperar el enlace a la colección.
- **Ritmo:** UPCH y UPC piden 10 s entre solicitudes. Recorrer las 71.596 fichas de UPC tomaría más de 8 días: conviene muestrear.
- **Código:** [`universidades_mvp/`](universidades_mvp/README.md) extrae las tesis de los 12 repositorios con un solo comando. 11 de ellos comparten el parser basado en metadatos DSpace; UPN y UNHEVAL usan además un parser de rótulos.
- **Datos personales:** las fichas incluyen nombres de autores y asesores, y algunas también DNI y jurados (`renati.*`). Esos campos se descartan; guardar solo lo necesario.
