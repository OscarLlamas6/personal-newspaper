# 🗞️ El Stack Diario

Tu periódico personal de tecnología: 1 o 2 correos al día con lo nuevo y lo que está en boca de todos
en DevOps, Cloud, Kubernetes, Data/MLOps, GenAI/agentes, seguridad (CVEs), lenguajes y virtualización.

**Costo: $0.** Corre en GitHub Actions (gratis en repos privados dentro del cupo mensual), envía por tu
Gmail y usa un LLM con capa gratuita para filtrar y resumir. Todo el código es tuyo y modificable.

## Cómo funciona

```
~140 fuentes (RSS/Atom, HN, CISA KEV, releases de GitHub, dev.to, Medium, Reddit…)
   → filtro: últimas 26 h, no repetidas, relevantes a tus temas
   → ranking: recencia + votos HN + afinidad con tus intereses + "buzz" (misma noticia en varias fuentes)
   → editor LLM: elige 2-5 por sección, resume en español y dice "por qué importa"
   → portada: "Si solo tienes 2 minutos" (titulares) + "Si tienes 20 minutos" (una cosa para explorar)
   → correo HTML estilo periódico (~15 min de lectura)
```

- **Edición matutina**: completa (~12 secciones, ~40 noticias).
- **Edición vespertina** (opcional): solo Portada, Seguridad e IA, máx. 2-3 cada una.
- **Certificaciones**: solo los lunes, con lo de la última semana.
- **Sin LLM** también funciona: usa el ranking heurístico y los extractos originales.

## Puesta en marcha (≈15 minutos)

1. **Crea un repo privado** en GitHub y sube esta carpeta tal cual.
   > Usa repo privado: en repos públicos GitHub desactiva los cron tras 60 días sin actividad.

2. **Contraseña de aplicación de Gmail** (requiere verificación en 2 pasos):
   Cuenta de Google → Seguridad → *Contraseñas de aplicaciones* → crea una para "Stack Diario".

3. **API key de Anthropic (Claude)** — crea una en console.anthropic.com y guárdala como secret `ANTHROPIC_API_KEY`.
   Opcional: variable `ANTHROPIC_MODEL` (por defecto `claude-opus-5-5`; `claude-haiku-5-5` o `claude-sonnet-5-5` son más baratos).

   *Alternativa: API key de un LLM gratuito* (cualquiera compatible con la API de OpenAI):

   | Proveedor | `LLM_BASE_URL` | Nota |
   |---|---|---|
   | Google Gemini (AI Studio) | `https://generativelanguage.googleapis.com/v1beta/openai/` | Capa gratuita generosa; usa un modelo "Flash" |
   | Groq | `https://api.groq.com/openai/v1` | Muy rápido; modelos abiertos (Llama, Qwen…) |
   | OpenRouter | `https://openrouter.ai/api/v1` | Modelos con sufijo `:free` |
   | Ollama (tu Proxmox) | `http://TU-HOST:11434/v1` | 100% local; ver "Correrlo en casa" |

   Los límites y nombres de modelos cambian seguido: copia el nombre vigente desde la página del proveedor.
   Cada edición hace ~13 llamadas pequeñas, cabe de sobra en las capas gratuitas.

4. **Secrets del repo** (Settings → Secrets and variables → Actions → New repository secret):

   | Secret | Ejemplo |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `465` |
   | `SMTP_USER` | `tucorreo@gmail.com` |
   | `SMTP_PASS` | la contraseña de aplicación (16 letras) |
   | `MAIL_TO` | `tucorreo@gmail.com` (varios separados por coma) |
   | `ANTHROPIC_API_KEY` | tu key de Anthropic (si la pones, ignora los `LLM_*`) |
   | `LLM_BASE_URL` | alternativa OpenAI-compatible: ver tabla de arriba |
   | `LLM_API_KEY` | tu key |
   | `LLM_MODEL` | nombre del modelo |

5. **Pruébalo**: pestaña *Actions* → *El Stack Diario* → *Run workflow*. Primero con "Solo generar"
   marcado (el HTML queda descargable en el run) y luego sin marcar para recibir el correo.

6. **Ajusta horarios** en `.github/workflows/stack-diario.yml` (están en UTC) y tu zona en
   `config/feeds.yaml → settings.timezone`. Si quieres un solo correo, borra la línea del cron vespertino.

## Personalizar

Todo vive en `config/feeds.yaml`:

- `profile`: quién eres. El LLM lo usa para decidir qué te importa — vale la pena afinarlo.
- `interests`: palabras que suben el puntaje (y filtran fuentes genéricas con `require_keywords: true`).
- Por sección: `max_items`, `evening: true`, `weekdays: [0]`, `window_hours`.
- Atajos de fuentes:
  ```yaml
  - { type: github_releases, repos: [owner/repo, ...] }   # releases estables (ignora rc/beta)
  - { type: hn_search, queries: [proxmox, nutanix], points: 20 }
  - { type: devto, tags: [kubernetes] }
  - { type: medium, tags: [devops] }
  - { type: reddit, subs: [sre] }
  - { type: trending, langs: [go, rust] }
  - { type: kev }                                          # CISA Known Exploited Vulnerabilities
  - { url: "https://...", name: "Mi blog", weight: 1.2, require_keywords: true }
  ```

**Tip — newsletters dentro del periódico:** muchas newsletters buenas (KubeWeekly, TLDR DevOps, Golang
Weekly, etc.) llegan por correo. Con [Kill the Newsletter](https://kill-the-newsletter.com/) (gratis y open
source) obtienes una dirección de correo que convierte lo que recibe en un feed Atom; suscríbete con esa
dirección y agrega el feed aquí. Así te llegan resumidas y sin duplicados en vez de llenar tu bandeja.

## Comandos locales

```bash
pip install -r requirements.txt
python -m digest.main --check-feeds                 # qué fuentes responden
python -m digest.main --demo                        # vista previa del diseño (out/demo.html)
python -m digest.main --edition morning --dry-run   # periódico real en out/, sin enviar
```

## Correrlo en casa (100% local y open source)

En un LXC/VM de tu Proxmox: instala Python y [Ollama](https://ollama.com) con un modelo de 7-14B,
pon las variables en un `.env` y agrega a `crontab`:

```cron
5 6  * * * cd /opt/stack-diario && set -a && . ./.env && python3 -m digest.main --edition morning
5 18 * * * cd /opt/stack-diario && set -a && . ./.env && python3 -m digest.main --edition evening
```

## Notas

- Gmail recorta correos HTML de más de ~102 KB; el script avisa si te acercas (baja `max_items`).
- Las fuentes que fallen un día se listan al pie del correo; no detienen el envío.
- Reddit a veces bloquea las IPs de GitHub Actions; por eso sus feeds son opcionales.
