# Traspaso — El Stack Diario

Documento para continuar el trabajo en Claude Code. Fecha: 8 de octubre de 2026.

## 1. Objetivo

El usuario es ingeniero de software senior. Pasó de dedicar ~5 h/día a aprender a tener 1-2 h, por tener 3 trabajos y una bebé recién nacida.
Quiere **1 o máximo 2 correos al día**, que se lean como un periódico, con lo nuevo y lo que está "en boca de todos". No busca ser experto; quiere tener el mapa al día. Las áreas que pidió:

- **Infraestructura y operación:** Data Engineering, MLOps, DevSecOps/DevOps, Cloud (AWS/Azure/GCP), Cloud Native, Kubernetes, SRE/SysAdmin, Platform Engineering, observabilidad, automatización y Harness.
- **IA:** LLMOps, sistemas agénticos y GenAI.
- **Fundamentos:** sistemas distribuidos, Linux y seguridad/CVEs.
- **Lenguajes:** Go, Python, Bash, Rust, Elixir, Zig, Erlang y TypeScript.
- **Virtualización:** Proxmox, Nutanix, CloudStack y VMware.
- **Otros:** certificaciones, y posts de la comunidad (Medium, dev.to, Hacker News).

**Restricciones:**
- Gratis y lo más open source posible.
- Solo para uso personal.
- Todo en español.

## 2. Decisiones tomadas

| Decisión | Motivo |
|---|---|
| Pipeline propio en Python en vez de una herramienta existente | Ninguna cubría todo el mapa en un solo correo. Las newsletters dan 10+ correos con duplicados; daily.dev y Feedly no envían un resumen por correo; FreshRSS y Miniflux agregan pero no filtran ni resumen. |
| Ejecución en **GitHub Actions** con cron, en un **repo privado** | Gratis. En repos públicos GitHub desactiva los cron tras 60 días sin actividad. |
| Alternativa documentada: cron en un LXC de Proxmox con Ollama | Opción 100% local y open source. |
| LLM **opcional** vía cualquier API compatible con OpenAI (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) | Sin atarse a un proveedor: sirve Gemini, Groq, OpenRouter (`:free`) u Ollama. Se usa `requests` directo, sin el SDK de OpenAI. |
| Si no hay LLM, ranking heurístico | El correo sale igual, con extractos originales y sin la caja "Si tienes 20 minutos". |
| Envío por SMTP (Gmail con contraseña de aplicación), puerto 465 SSL o 587 STARTTLS | Gratis y simple. |
| Estado "ya enviado" en `state/seen.json`, persistido con `actions/cache` (no commits) | Evita repetir noticias sin ensuciar el historial. Las entradas se purgan a los 21 días. |
| Dos ediciones: **morning** completa y **evening** opcional | La vespertina solo incluye secciones con `evening: true` (portada, seguridad, IA) y una ventana de 12 h. |
| Certificaciones solo los lunes, con ventana de 168 h | Es un tema de baja frecuencia. |
| Correo HTML con estilos inline y tablas | Compatibilidad con clientes de correo. Gmail recorta mensajes de más de ~102 KB; el script avisa a partir de 95 KB. |
| Toda la configuración de contenido vive en `config/feeds.yaml` | El usuario puede ajustar fuentes y cantidades sin tocar el código. |
| Reddit y algunas fuentes inestables marcadas `optional: true` | Reddit suele bloquear las IPs de CI. Las opcionales no aparecen como "fallidas" en el pie del correo. |
| Horario por defecto: 11:05 y 23:05 UTC (6:05 am y pm en UTC-5) | Supuesto mío. **No se confirmó la zona horaria del usuario.** |

## 3. Arquitectura (`digest/main.py`)

```
load_config → select_sections (filtra por edición/día) → expand_feeds (atajos → URLs)
→ fetch_all (ThreadPool, 12 workers; una fuente caída no detiene nada)
→ filter_items (ventana de tiempo, seen, require_keywords)
→ score_items (peso × [recencia + log(puntos HN) + afinidad con intereses + buzz entre fuentes + ransomware])
→ shortlist (dedupe por URL canónica, tope por fuente, excluye lo ya usado en secciones previas)
→ curate_section (LLM elige k y resume en JSON; fallback heurístico)
→ editorial (titulares + "explorar" con url validada contra la lista)
→ build_ctx → render (Jinja2) + to_text → send_email → save_seen
```

**Atajos de feeds en el YAML:**

| Tipo | Se convierte en |
|---|---|
| `github_releases` | `releases.atom`; descarta rc/beta/alpha/preview y antepone el nombre del repo al título |
| `devto` | feed por tag de dev.to |
| `medium` | feed por tag de Medium, con `require_keywords` por defecto |
| `hn_search` | `hnrss.org/newest?q=…&points=N` |
| `reddit` | top diario del subreddit, opcional |
| `trending` | feeds de GitHub Trending de `mshibanami.github.io/GitHubTrendingRSS` |
| `kev` | JSON de CISA KEV |

**CLI:**
- `--edition morning|evening` elige la edición.
- `--dry-run` genera el HTML en `out/` sin enviar.
- `--check-feeds` valida que las fuentes respondan.
- `--demo` renderiza datos ficticios.
- `-v` activa el modo detallado.

## 4. Archivos

| Archivo | Contenido |
|---|---|
| `digest/main.py` | Todo el pipeline: modelo `Item`, descarga, filtro, ranking, LLM, render, SMTP y CLI |
| `digest/sample.py` | Datos ficticios para `--demo` |
| `config/feeds.yaml` | `settings`, `profile` (contexto para el LLM), `interests` (palabras clave) y 14 secciones con unas 145 fuentes ya expandidas |
| `templates/newspaper.html` | Plantilla del correo (Jinja2, estilos inline) |
| `.github/workflows/stack-diario.yml` | Dos crons, `workflow_dispatch` (edición + dry_run), restaurar/guardar caché de `state/` y subida del HTML como artifact por 30 días |
| `tests/test_e2e.py` | Prueba de punta a punta sin red: feeds, KEV, LLM y SMTP simulados. Se corre con `python tests/test_e2e.py` |
| `README.md` | Guía de puesta en marcha para el usuario |
| `requirements.txt` | feedparser, requests, PyYAML, Jinja2 |
| `.gitignore` | Excluye `out/`, `state/` y `.env` |

## 5. Estado actual

**Hecho y probado:**
- La prueba de punta a punta pasa con red simulada. Verifica:
  - la ventana de tiempo de KEV y de los feeds;
  - el descarte de pre-releases;
  - `require_keywords`;
  - la deduplicación entre secciones;
  - que no se repitan noticias en la segunda ejecución;
  - que las fuentes caídas se listen y las opcionales no;
  - la edición vespertina;
  - el modo sin LLM.
- El HTML de prueba pesa unos 44 KB.
- La demo se renderizó y se revisó visualmente con Playwright.
- Las URLs dudosas se verificaron con un fetch externo:
  - **Responden bien:** Azure Updates, PlatformEngineering.org, Harness, Zig News, Proxmox Announcements, el feed de CVEs de Kubernetes, GitHub Trending (`all.xml`), LF Training, oss-security y Data Engineering Weekly.
  - **Corregido:** Python Insider se mudó a `https://blog.python.org/rss.xml`.
  - **Marcadas `optional`** porque bloquean al fetcher: CISA Advisories, Start Data Engineering y Lobsters por tags.

**No probado todavía:**
- **Descargas reales de los ~145 feeds.** El sandbox tenía una lista de dominios permitidos y casi todo falló por eso, no por las URLs. Los `releases.atom` de GitHub dieron 403 a través del proxy del sandbox; probablemente funcionan desde Actions, pero hay que confirmarlo.
- Llamadas reales a un proveedor LLM: parseo del JSON, límites de tasa y calidad de los resúmenes.
- El envío real por Gmail.
- El workflow de GitHub Actions corriendo en GitHub.

**El usuario aún no:**
- creó el repo;
- configuró los secrets;
- eligió proveedor de LLM;
- confirmó su zona horaria.

## 6. Próximos pasos

1. **Validar las fuentes reales con red abierta:** `python -m digest.main --check-feeds`. Corregir o retirar las que fallen, en especial los `releases.atom` de GitHub, hnrss, Medium y dev.to.
2. **Ejecución real sin LLM:** `python -m digest.main --edition morning --dry-run`. Revisar `out/*.html`: volumen por sección, calidad del ranking y peso en KB.
3. **Ejecución con un LLM real** (por ejemplo Gemini Flash vía el endpoint compatible con OpenAI). Ajustar:
   - `LLM_SLEEP` y los reintentos según los límites de tasa;
   - los prompts `SECTION_PROMPT` y `EDITORIAL_PROMPT` si los resúmenes salen genéricos;
   - el `max_tokens` (1800) si las respuestas JSON llegan truncadas.
4. **Probar el envío SMTP** con la contraseña de aplicación de Gmail.
5. **Subir a un repo privado de GitHub**, crear los secrets (`SMTP_*`, `MAIL_TO`, `LLM_*`) y lanzar el workflow con `dry_run=true` y luego `false`.
6. **Confirmar la zona horaria** del usuario y ajustar:
   - los dos crons del workflow;
   - la comparación literal `"5 23 * * *"` en el paso "Elegir edición", que debe coincidir con el cron vespertino;
   - `settings.timezone`.
7. **Afinar tras unos días de uso real:**
   - `max_items` por sección (el objetivo es leer en unos 15 minutos);
   - los pesos de las fuentes;
   - la lista de `interests`;
   - el `profile` del lector.

**Mejoras opcionales a considerar:**
- Integrar newsletters por correo vía Kill the Newsletter (correo → Atom) como fuentes adicionales.
- Un resumen semanal los domingos.
- Un archivo navegable en GitHub Pages.
- Convertir `tests/test_e2e.py` a pytest y correrlo en CI.
- Reintentos HTTP en `http_get`.
- Un feed de GitHub Security Advisories filtrado por ecosistema (Go, PyPI, npm, crates).

## 7. Notas para quien continúe

- **El estado se guarda solo en envíos reales.** `run()` marca como vistas las noticias únicamente cuando se envía el correo, no en `--dry-run`. Por eso varias ejecuciones de prueba seguidas mostrarán las mismas noticias.
- **Lo que no trae fecha** se marca como visto tras el primer envío. La primera ejecución sin estado puede traer entradas viejas de feeds sin fecha; el tope por feed (`max_entries: 60`) y el LLM lo amortiguan.
- **El buzz por título** solo cuenta títulos de 25 caracteres o más normalizados, para no confundir versiones como `v1.2.3` de repos distintos.
- **El código y los textos están en español**, que es el idioma del usuario. Mantén ese idioma.
