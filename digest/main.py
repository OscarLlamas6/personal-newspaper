#!/usr/bin/env python3
"""
El Stack Diario — tu periódico personal de tecnología.

Uso:
  python -m digest.main --edition morning              # genera y envía por correo
  python -m digest.main --edition evening              # edición corta (solo lo urgente)
  python -m digest.main --edition morning --dry-run    # genera el HTML sin enviar
  python -m digest.main --check-feeds                  # valida que todas las fuentes respondan
  python -m digest.main --demo                         # renderiza con datos de ejemplo (sin red)

Variables de entorno:
  SMTP_HOST, SMTP_PORT (465 SSL | 587 STARTTLS), SMTP_USER, SMTP_PASS, MAIL_TO, MAIL_FROM
  ANTHROPIC_API_KEY [, ANTHROPIC_MODEL, ANTHROPIC_EFFORT]   (Claude, nativo; opcional)
  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL   (alternativa: cualquier API compatible con OpenAI)
  LLM_SLEEP (segundos entre llamadas al LLM, por defecto 4)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import logging
import math
import os
import re
import smtplib
import ssl
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "feeds.yaml"
STATE_PATH = ROOT / "state" / "seen.json"
OUT_DIR = ROOT / "out"
TEMPLATES = ROOT / "templates"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
UA = "Mozilla/5.0 (compatible; StackDiario/1.0; +personal news digest)"

log = logging.getLogger("stack-diario")

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

TAGS = {
    "en-boca": ("🔥", "En boca de todos"),
    "radar": ("🧭", "Para el radar"),
    "practico": ("🛠️", "Práctico"),
    "seguridad": ("🚨", "Seguridad"),
    "lanzamiento": ("🚀", "Lanzamiento"),
}
PRERELEASE_RE = re.compile(r"(alpha|beta|[-.]rc|\brc\d|preview|nightly|snapshot|\bdev\d*\b)", re.I)


# ----------------------------------------------------------------------------- modelo

@dataclass
class Item:
    title: str
    url: str
    source: str
    published: dt.datetime | None = None
    summary: str = ""
    extra: dict = field(default_factory=dict)
    weight: float = 1.0
    require_keywords: bool = False
    score: float = 0.0
    resumen: str = ""
    por_que: str = ""
    etiqueta: str = ""

    @property
    def key(self) -> str:
        return canon(self.url)


# ----------------------------------------------------------------------------- utilidades

TRACKING_RE = re.compile(r"^(utm_|ref$|source$|fbclid$|gclid$|mc_)", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def canon(url: str) -> str:
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING_RE.match(k)]
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", urlencode(q), ""))


def clean(text: str | None, limit: int = 400) -> str:
    t = unescape(TAG_RE.sub(" ", text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limit:
        return t
    return t[:limit].rsplit(" ", 1)[0] + "…"


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()[:90]


def build_interests_re(keywords: list[str]) -> re.Pattern:
    kws = sorted({k.strip() for k in keywords if k and k.strip()}, key=len, reverse=True)
    if not kws:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in kws) + r")(?![\w-])", re.I)


def keyword_hits(rx: re.Pattern, text: str) -> int:
    return len({m.lower() for m in rx.findall(text)})


def fecha_es(d: dt.datetime) -> str:
    return f"{DIAS[d.weekday()].capitalize()}, {d.day} de {MESES[d.month - 1]} de {d.year}"


# ----------------------------------------------------------------------------- configuración

def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def expand_feeds(raw_feeds: list) -> list[dict]:
    """Convierte los atajos del YAML (github_releases, devto, hn_search, ...) en feeds concretos."""
    out: list[dict] = []
    for f in raw_feeds or []:
        if isinstance(f, str):
            out.append({"type": "rss", "url": f})
            continue
        f = dict(f)
        t = f.pop("type", "rss")
        common = {k: v for k, v in f.items() if k in ("weight", "require_keywords", "optional", "max_entries")}
        if t == "github_releases":
            for repo in f["repos"]:
                out.append({"type": "rss", "url": f"https://github.com/{repo}/releases.atom", "name": repo,
                            "github_release": True, **({"weight": 0.9} | common)})
        elif t == "devto":
            for tag in f["tags"]:
                out.append({"type": "rss", "url": f"https://dev.to/feed/tag/{tag}", "name": f"dev.to #{tag}",
                            **({"weight": 0.7} | common)})
        elif t == "medium":
            for tag in f["tags"]:
                out.append({"type": "rss", "url": f"https://medium.com/feed/tag/{tag}", "name": f"Medium #{tag}",
                            **({"weight": 0.6, "require_keywords": True} | common)})
        elif t == "hn_search":
            pts = f.get("points", 40)
            for q in f["queries"]:
                out.append({"type": "rss", "url": f"https://hnrss.org/newest?q={quote_plus(q)}&points={pts}",
                            "name": f"HN · {q}", **({"weight": 1.0} | common)})
        elif t == "reddit":
            # Reddit a veces bloquea IPs de CI: se marcan opcionales para no ensuciar el reporte.
            for sub in f["subs"]:
                out.append({"type": "rss", "url": f"https://www.reddit.com/r/{sub}/top/.rss?t=day",
                            "name": f"r/{sub}", **({"weight": 0.7, "optional": True} | common)})
        elif t == "trending":
            for lang in f["langs"]:
                out.append({"type": "rss",
                            "url": f"https://mshibanami.github.io/GitHubTrendingRSS/daily/{lang}.xml",
                            "name": f"GitHub Trending · {lang}", **({"weight": 0.9} | common)})
        elif t == "kev":
            out.append({"type": "kev", "url": f.get("url", KEV_URL), "name": "CISA KEV",
                        "weight": f.get("weight", 2.5)})
        else:
            f["type"] = "rss"
            out.append(f)
    return out


def select_sections(cfg: dict, edition: str, local_now: dt.datetime) -> list[dict]:
    sections = []
    for s in cfg["sections"]:
        if edition == "evening" and not s.get("evening"):
            continue
        wd = s.get("weekdays")
        if edition == "morning" and wd is not None and local_now.weekday() not in wd:
            continue
        s = dict(s)
        s["_feeds"] = expand_feeds(s.get("feeds"))
        sections.append(s)
    return sections


# ----------------------------------------------------------------------------- descarga

def http_get(url: str, timeout: int = 25) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=timeout)
    r.raise_for_status()
    return r


def parse_rss(cfg: dict) -> list[Item]:
    r = http_get(cfg["url"])
    parsed = feedparser.parse(r.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"feed inválido: {parsed.get('bozo_exception')}")
    source = cfg.get("name") or clean(parsed.feed.get("title", ""), 60) or urlparse(cfg["url"]).netloc
    items: list[Item] = []
    for e in parsed.entries[: cfg.get("max_entries", 60)]:
        link, title = e.get("link"), clean(e.get("title", ""), 220)
        if not link or not title:
            continue
        if cfg.get("github_release"):
            if PRERELEASE_RE.search(title):
                continue
            repo_short = source.split("/")[-1]
            if repo_short.lower() not in title.lower():
                title = f"{source} {title}"
        ts = e.get("published_parsed") or e.get("updated_parsed")
        pub = dt.datetime(*ts[:6], tzinfo=dt.timezone.utc) if ts else None
        raw = e.get("summary") or (e.get("content") or [{}])[0].get("value", "")
        summary = clean(raw, 600)
        extra: dict = {}
        if m := re.search(r"Points:\s*(\d+)", summary):
            extra["points"] = int(m.group(1))
        if m := re.search(r"#\s*Comments:\s*(\d+)", summary):
            extra["comments"] = int(m.group(1))
        if e.get("comments") and e["comments"] != link:
            extra["discussion"] = e["comments"]
        if "points" in extra:  # en hnrss el resumen es solo metadata
            summary = ""
        items.append(Item(title=title, url=link, source=source, published=pub, summary=summary,
                          extra=extra, weight=float(cfg.get("weight", 1.0)),
                          require_keywords=bool(cfg.get("require_keywords", False)),
                          etiqueta="lanzamiento" if cfg.get("github_release") else ""))
    return items


def parse_kev(cfg: dict) -> list[Item]:
    data = http_get(cfg["url"]).json()
    items = []
    for v in data.get("vulnerabilities", []):
        try:
            added = dt.datetime.strptime(v["dateAdded"], "%Y-%m-%d").replace(hour=12, tzinfo=dt.timezone.utc)
        except (KeyError, ValueError):
            continue
        cve = v.get("cveID", "")
        title = f"{cve} · {v.get('vendorProject', '')} {v.get('product', '')}: {v.get('vulnerabilityName', '')}"
        summary = clean(f"{v.get('shortDescription', '')} Acción requerida: {v.get('requiredAction', '')} "
                        f"(fecha límite federal: {v.get('dueDate', 'n/d')})", 600)
        extra = {"ransomware": v.get("knownRansomwareCampaignUse") == "Known"}
        items.append(Item(title=clean(title, 220), url=f"https://nvd.nist.gov/vuln/detail/{cve}",
                          source="CISA KEV", published=added, summary=summary, extra=extra,
                          weight=float(cfg.get("weight", 2.5)), etiqueta="seguridad"))
    return items


def fetch_one(cfg: dict) -> list[Item]:
    return parse_kev(cfg) if cfg.get("type") == "kev" else parse_rss(cfg)


def fetch_all(sections: list[dict], workers: int = 12) -> tuple[dict[str, list[Item]], list[str], int]:
    results: dict[str, list[Item]] = {s["id"]: [] for s in sections}
    failures: list[str] = []
    jobs = [(s["id"], f) for s in sections for f in s["_feeds"]]
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, f): (sid, f) for sid, f in jobs}
        for fut in cf.as_completed(futs):
            sid, f = futs[fut]
            name = f.get("name") or f["url"]
            try:
                results[sid].extend(fut.result())
            except Exception as e:  # noqa: BLE001 — una fuente rota no debe tumbar el periódico
                if f.get("optional"):
                    log.info("Fuente opcional sin respuesta: %s (%s)", name, e)
                else:
                    log.warning("Fuente falló: %s (%s)", name, e)
                    failures.append(name)
    return results, sorted(set(failures)), len(jobs)


# ----------------------------------------------------------------------------- filtro y ranking

def filter_items(items: list[Item], now: dt.datetime, window_h: float, seen: dict,
                 rx: re.Pattern) -> list[Item]:
    cutoff = now - dt.timedelta(hours=window_h)
    out = []
    for it in items:
        if it.key in seen:
            continue
        if it.published:
            if it.published < cutoff:
                continue
            if it.published > now + dt.timedelta(hours=6):
                it.published = now
        if it.require_keywords and not rx.search(f"{it.title} {it.summary}"):
            continue
        out.append(it)
    return out


def score_items(by_section: dict[str, list[Item]], now: dt.datetime, window_h: float, rx: re.Pattern) -> None:
    """Puntaje = peso de la fuente × (recencia + popularidad HN + afinidad con tus temas + 'buzz')."""
    sources_by_key: dict[str, set] = defaultdict(set)

    def keys(it: Item) -> list[str]:
        nt = norm_title(it.title)
        return [it.key] + ([f"t:{nt}"] if len(nt) >= 25 else [])

    for items in by_section.values():
        for it in items:
            for k in keys(it):
                sources_by_key[k].add(it.source)
    for items in by_section.values():
        for it in items:
            buzz = max(len(sources_by_key[k]) for k in keys(it)) - 1
            it.extra["buzz"] = buzz
            age_h = (now - it.published).total_seconds() / 3600 if it.published else window_h / 2
            recency = max(0.0, 1 - age_h / max(window_h, 1))
            pts = it.extra.get("points", 0)
            hits = keyword_hits(rx, f"{it.title} {it.summary}")
            ransom = 0.5 if it.extra.get("ransomware") else 0
            it.score = it.weight * (1 + recency + math.log1p(pts) / 2.5 + min(hits, 3) * 0.4 + buzz * 0.8 + ransom)


def shortlist(items: list[Item], used: set, limit: int, per_source: int) -> list[Item]:
    best: dict[str, Item] = {}
    for it in items:
        if it.key in used:
            continue
        if it.key not in best or it.score > best[it.key].score:
            best[it.key] = it
    out, per = [], Counter()
    for it in sorted(best.values(), key=lambda i: i.score, reverse=True):
        if per[it.source] >= per_source:
            continue
        per[it.source] += 1
        out.append(it)
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------------------------- LLM (opcional)

SYSTEM = (
    "Eres el editor jefe de un periódico técnico diario para un ingeniero de software senior con muy poco "
    "tiempo. Tu trabajo es separar la señal del ruido. Prioriza: lanzamientos y cambios que de verdad importan, "
    "herramientas y proyectos que están ganando tracción, vulnerabilidades explotables en su stack, ideas que "
    "toda la comunidad está discutiendo. Descarta: marketing, tutoriales básicos, listicles genéricos, clickbait, "
    "notas repetidas y releases de parche sin novedades. Escribe en español neutro, conciso, sin hype ni "
    "emojis. Nunca inventes datos que no estén en el material. Responde SOLO con JSON válido."
)

SECTION_PROMPT = """Sección: {title} — {description}
Perfil del lector: {profile}

Elige como máximo {k} noticias de la lista (menos si no hay suficientes que valgan la pena; puede ser cero).
Candidatas:
{lines}

Devuelve exactamente este JSON:
{{"tendencia": "una frase sobre qué se mueve hoy en esta área, o cadena vacía",
  "items": [{{"id": <número de la lista>,
             "resumen": "1-2 frases: qué es o qué pasó",
             "por_que": "1 frase: por qué le importa a este lector",
             "etiqueta": "en-boca | radar | practico | seguridad | lanzamiento"}}]}}"""

EDITORIAL_PROMPT = """Estas son las noticias seleccionadas hoy para el periódico:
{lines}

Escribe la portada. Devuelve exactamente este JSON:
{{"titulares": ["3 a 5 frases cortas (máx. 25 palabras c/u) con lo más importante del día"],
  "explorar": {{"url": "copia EXACTA de una url de la lista",
               "por_que": "por qué vale la pena dedicarle 20-30 minutos hoy",
               "plan": "qué hacer concretamente en esos minutos (probar X, leer Y, revisar Z en tu stack)"}}}}"""


class LLM:
    """Proveedor Anthropic nativo (ANTHROPIC_API_KEY) o cualquier API compatible con OpenAI (LLM_*)."""

    def __init__(self) -> None:
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base = (os.getenv("LLM_BASE_URL") or "").rstrip("/")
        self.key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "")
        self.client = None
        if self.anthropic_key:
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.anthropic_key, max_retries=4, timeout=180)
            self.model = os.getenv("ANTHROPIC_MODEL") or "claude-opus-5-5"
            self.base = "api.anthropic.com"
            self.enabled = True
        else:
            self.enabled = bool(self.base and self.model)
        self.sleep = float(os.getenv("LLM_SLEEP", "4"))

    def _chat_anthropic(self, system: str, user: str) -> str | None:
        import anthropic
        try:
            # El pensamiento cuenta contra max_tokens: margen amplio y esfuerzo bajo (tarea de resumen).
            r = self.client.messages.create(
                model=self.model, max_tokens=8000, system=system,
                output_config={"effort": os.getenv("ANTHROPIC_EFFORT", "low")},
                messages=[{"role": "user", "content": user}])
        except anthropic.APIError as e:
            log.warning("Anthropic error: %s", e)
            return None
        if r.stop_reason == "refusal":
            log.warning("Anthropic rechazó la solicitud (%s)", getattr(r.stop_details, "category", None))
            return None
        return "".join(b.text for b in r.content if b.type == "text") or None

    def chat(self, system: str, user: str, max_tokens: int = 1800) -> str | None:
        if self.client:
            return self._chat_anthropic(system, user)
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = {"model": self.model, "temperature": 0.2, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        for attempt in range(4):
            try:
                r = requests.post(f"{self.base}/chat/completions", headers=headers, json=body, timeout=180)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = int(r.headers.get("retry-after", 0) or 0) or 10 * (2 ** attempt)
                    log.info("LLM %s, reintento en %ss", r.status_code, wait)
                    time.sleep(min(wait, 90))
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except (requests.RequestException, KeyError, ValueError) as e:
                log.warning("LLM error (intento %d): %s", attempt + 1, e)
                time.sleep(5 * (attempt + 1))
        return None

    def json(self, system: str, user: str) -> dict | None:
        txt = self.chat(system, user)
        if not txt:
            return None
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)  # modelos de razonamiento
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def curate_section(llm: LLM, sec: dict, cands: list[Item], k: int, profile: str) -> tuple[list[Item], str]:
    if not cands:
        return [], ""
    if llm.enabled:
        lines = []
        for n, it in enumerate(cands, 1):
            meta = [it.source]
            if it.extra.get("points"):
                meta.append(f"{it.extra['points']} pts HN")
            if it.extra.get("buzz"):
                meta.append(f"aparece en {it.extra['buzz'] + 1} fuentes")
            lines.append(f"[{n}] {it.title} — {' · '.join(meta)}\n    {clean(it.summary, 240)}")
        res = llm.json(SYSTEM, SECTION_PROMPT.format(title=sec["title"], description=sec.get("description", ""),
                                                     profile=profile, k=k, lines="\n".join(lines)))
        time.sleep(llm.sleep)
        if isinstance(res, dict) and isinstance(res.get("items"), list):
            picked, ids = [], set()
            for p in res["items"]:
                try:
                    idx = int(p.get("id"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if 1 <= idx <= len(cands) and idx not in ids:
                    ids.add(idx)
                    it = cands[idx - 1]
                    it.resumen = clean(p.get("resumen", ""), 420)
                    it.por_que = clean(p.get("por_que", ""), 260)
                    if p.get("etiqueta") in TAGS:
                        it.etiqueta = p["etiqueta"]
                    picked.append(it)
                if len(picked) >= k:
                    break
            return picked, clean(res.get("tendencia", ""), 300)
        log.warning("LLM sin respuesta útil en '%s'; uso ranking heurístico", sec["id"])
    picked = cands[:k]
    for it in picked:
        it.resumen = clean(it.summary, 260)
    return picked, ""


def editorial(llm: LLM, out_sections: list[dict]) -> tuple[list[str], dict | None]:
    all_items = [(s, it) for s in out_sections for it in s["_items"]]
    if not all_items:
        return [], None
    if llm.enabled:
        lines = "\n".join(f"- [{s['title']}] {it.title} — {it.resumen or clean(it.summary, 160)} ({it.url})"
                          for s, it in all_items)
        res = llm.json(SYSTEM, EDITORIAL_PROMPT.format(lines=lines))
        if isinstance(res, dict):
            titulares = [clean(t, 240) for t in res.get("titulares", []) if isinstance(t, str) and t.strip()][:5]
            by_url = {it.url: it for _, it in all_items}
            exp, explorar = res.get("explorar") or {}, None
            if isinstance(exp, dict) and exp.get("url") in by_url:
                it = by_url[exp["url"]]
                explorar = {"title": it.title, "url": it.url, "source": it.source,
                            "por_que": clean(exp.get("por_que", ""), 320), "plan": clean(exp.get("plan", ""), 320)}
            if titulares:
                return titulares, explorar
    top = sorted((it for _, it in all_items), key=lambda i: i.score, reverse=True)[:5]
    return [it.title for it in top], None


# ----------------------------------------------------------------------------- estado (no repetir noticias)

def load_seen(now: dt.datetime) -> dict:
    try:
        data = json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    cutoff = (now - dt.timedelta(days=21)).isoformat()
    return {k: v for k, v in data.items() if v >= cutoff}


def save_seen(seen: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(seen, indent=0, sort_keys=True))


# ----------------------------------------------------------------------------- render

def item_ctx(it: Item) -> dict:
    emoji, label = TAGS.get(it.etiqueta, ("", ""))
    return {"title": it.title, "url": it.url, "source": it.source, "resumen": it.resumen,
            "por_que": it.por_que, "tag_emoji": emoji, "tag_label": label,
            "points": it.extra.get("points"), "comments": it.extra.get("comments"),
            "discussion": it.extra.get("discussion"), "buzz": it.extra.get("buzz", 0),
            "ransomware": it.extra.get("ransomware", False)}


def to_text(ctx: dict) -> str:
    lines = [f"EL STACK DIARIO — {ctx['edicion']}", ctx["fecha"], ""]
    if ctx["titulares"]:
        lines += ["EN 2 MINUTOS"] + [f"  • {t}" for t in ctx["titulares"]] + [""]
    if ctx["explorar"]:
        e = ctx["explorar"]
        lines += ["SI TIENES 20 MINUTOS", f"  {e['title']}", f"  {e['por_que']}", f"  Plan: {e['plan']}",
                  f"  {e['url']}", ""]
    for s in ctx["secciones"]:
        lines.append(f"== {s['title'].upper()} ==")
        if s["trend"]:
            lines.append(f"  ({s['trend']})")
        for it in s["items"]:
            lines.append(f"- {it['title']} [{it['source']}]")
            if it["resumen"]:
                lines.append(f"  {it['resumen']}")
            if it["por_que"]:
                lines.append(f"  Por qué importa: {it['por_que']}")
            lines.append(f"  {it['url']}")
        lines.append("")
    return "\n".join(lines)


def render(ctx: dict) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    return env.get_template("newspaper.html").render(**ctx)


def build_ctx(*, local_now, edition, out_sections, titulares, explorar, stats, demo=False) -> dict:
    ctx = {
        "fecha": fecha_es(local_now),
        "edicion": "Edición vespertina · solo lo urgente" if edition == "evening" else "Edición matutina",
        "titulares": titulares, "explorar": explorar, "demo": demo, "stats": stats,
        "secciones": [{"title": s["title"], "emoji": s.get("emoji", ""), "trend": s.get("_trend", ""),
                       "items": [item_ctx(i) for i in s["_items"]]} for s in out_sections],
    }
    ctx["n_items"] = sum(len(s["items"]) for s in ctx["secciones"])
    ctx["lectura_min"] = max(1, round(len(to_text({**ctx, "lectura_min": 0}).split()) / 200))
    return ctx


# ----------------------------------------------------------------------------- correo

def send_email(subject: str, html_body: str, text_body: str) -> None:
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "465"))
    user, pwd = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    to = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]
    sender = os.getenv("MAIL_FROM") or user
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"El Stack Diario <{sender}>", ", ".join(to)
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain="stack-diario.local")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, pwd)
            s.sendmail(sender, to, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.sendmail(sender, to, msg.as_string())


# ----------------------------------------------------------------------------- comandos

def run(edition: str, dry_run: bool) -> int:
    cfg = load_config()
    st = cfg.get("settings", {})
    now = dt.datetime.now(dt.timezone.utc)
    local_now = now.astimezone(ZoneInfo(st.get("timezone", "UTC")))
    evening = edition == "evening"
    sections = select_sections(cfg, edition, local_now)
    rx = build_interests_re(cfg.get("interests", []))
    seen = load_seen(now)

    log.info("Descargando %d fuentes en %d secciones…", sum(len(s["_feeds"]) for s in sections), len(sections))
    raw, failures, n_feeds = fetch_all(sections)
    base_window = st.get("evening_window_hours", 12) if evening else st.get("window_hours", 26)
    filtered = {s["id"]: filter_items(raw[s["id"]], now,
                                      base_window if evening else s.get("window_hours", base_window), seen, rx)
                for s in sections}
    score_items(filtered, now, base_window, rx)
    n_cands = sum(len(v) for v in filtered.values())
    log.info("%d candidatas tras filtrar", n_cands)

    llm = LLM()
    log.info("LLM: %s", f"{llm.model} @ {llm.base}" if llm.enabled else "desactivado (modo heurístico)")
    used: set[str] = set()
    out_sections = []
    for s in sections:
        k = s.get("evening_max_items", 2) if evening else s.get("max_items", 4)
        cands = shortlist(filtered[s["id"]], used, st.get("candidates_per_section", 25), s.get("max_per_source", 4))
        picked, trend = curate_section(llm, s, cands, k, cfg.get("profile", ""))
        used.update(i.key for i in picked)
        if picked:
            out_sections.append({**s, "_items": picked, "_trend": trend})
        log.info("  %-14s %2d candidatas → %d elegidas", s["id"], len(cands), len(picked))

    titulares, explorar = editorial(llm, out_sections) if not evening or out_sections else ([], None)
    ctx = build_ctx(local_now=local_now, edition=edition, out_sections=out_sections, titulares=titulares,
                    explorar=explorar, stats={"fuentes": n_feeds, "candidatas": n_cands, "fallidas": failures,
                                              "llm": llm.model if llm.enabled else ""})
    html_body, text_body = render(ctx), to_text(ctx)
    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / f"stack-diario-{local_now:%Y-%m-%d}-{edition}.html"
    out_file.write_text(html_body, encoding="utf-8")
    size_kb = len(html_body.encode()) / 1024
    log.info("HTML: %s (%.0f KB)", out_file, size_kb)
    if size_kb > 95:
        log.warning("El HTML pesa %.0f KB: Gmail recorta correos de más de ~102 KB. Baja max_items.", size_kb)

    if not out_sections:
        log.info("Nada nuevo que valga la pena; no se envía correo.")
        return 0
    if dry_run:
        return 0

    corto = f"{local_now.day} {MESES[local_now.month - 1][:3]}"
    lead = clean(titulares[0], 70) if titulares else ""
    prefix = "🗞️" if not evening else "🌙"
    send_email(f"{prefix} El Stack Diario · {corto}" + (f" — {lead}" if lead else ""), html_body, text_body)
    log.info("Correo enviado.")

    stamp = now.isoformat()
    for s in out_sections:
        for it in s["_items"]:
            seen[it.key] = stamp
    for items in raw.values():  # lo que no trae fecha se marca para no reaparecer mañana
        for it in items:
            if it.published is None:
                seen.setdefault(it.key, stamp)
    save_seen(seen)
    return 0


def check_feeds() -> int:
    cfg = load_config()
    feeds = [(s["id"], f) for s in cfg["sections"] for f in expand_feeds(s.get("feeds"))]
    bad = 0
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(fetch_one, f): (sid, f) for sid, f in feeds}
        rows = []
        for fut in cf.as_completed(futs):
            sid, f = futs[fut]
            try:
                n = len(fut.result())
                rows.append((sid, "OK  ", f"{n:3d} entradas", f.get("name") or f["url"], ""))
            except Exception as e:  # noqa: BLE001
                tag = "opc." if f.get("optional") else "FAIL"
                bad += 0 if f.get("optional") else 1
                rows.append((sid, tag, "", f.get("name") or f["url"], f"{f['url']} → {str(e)[:90]}"))
    for r in sorted(rows):
        print(f"{r[1]} {r[0]:<14} {r[2]:<13} {r[3]}" + (f"\n       {r[4]}" if r[4] else ""))
    print(f"\n{len(feeds)} fuentes, {bad} con error (opcionales excluidas).")
    return 1 if bad else 0


def demo() -> int:
    from digest.sample import demo_sections
    out_sections, titulares, explorar = demo_sections()
    ctx = build_ctx(local_now=dt.datetime.now(ZoneInfo("UTC")), edition="morning", out_sections=out_sections,
                    titulares=titulares, explorar=explorar, demo=True,
                    stats={"fuentes": 112, "candidatas": 486, "fallidas": ["Zig News"], "llm": "demo"})
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "demo.html"
    out.write_text(render(ctx), encoding="utf-8")
    print(out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="El Stack Diario")
    ap.add_argument("--edition", choices=["morning", "evening"], default="morning")
    ap.add_argument("--dry-run", action="store_true", help="genera el HTML en out/ sin enviar correo")
    ap.add_argument("--check-feeds", action="store_true", help="valida todas las fuentes")
    ap.add_argument("--demo", action="store_true", help="renderiza out/demo.html con datos de ejemplo")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")
    if a.check_feeds:
        return check_feeds()
    if a.demo:
        return demo()
    return run(a.edition, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
