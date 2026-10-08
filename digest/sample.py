"""Datos FICTICIOS para `--demo`: sirven solo para ver el diseño del periódico sin red ni LLM."""
from digest.main import Item


def _it(title, source, resumen, por_que, etiqueta="", **extra):
    it = Item(title=title, url="https://example.com/" + str(abs(hash(title)) % 10**8), source=source,
              resumen=resumen, por_que=por_que, etiqueta=etiqueta, extra={"buzz": 0, **extra})
    return it


def demo_sections():
    secs = [
        {"title": "Lo más comentado", "emoji": "🔥",
         "_trend": "La conversación del día gira en torno a costos de inferencia y a herramientas de build en Rust.",
         "_items": [
             _it("Ejemplo: Una startup migra su plataforma de Kubernetes a VMs simples y explica por qué",
                 "Hacker News", "Post largo con números de costo y complejidad operativa antes y después de la migración.",
                 "Buen caso para contrastar cuándo K8s sí paga su complejidad en tus propios proyectos.",
                 "en-boca", points=842, comments=513, buzz=2, discussion="https://example.com/hn"),
             _it("Ejemplo: Show HN — un orquestador de pipelines escrito en Zig de un solo binario",
                 "Show HN", "Proyecto nuevo que reemplaza un stack de tres herramientas con un binario sin dependencias.",
                 "Toca dos de tus intereses a la vez: data engineering y Zig.", "radar", points=311, comments=97),
         ]},
        {"title": "Seguridad y CVEs", "emoji": "🚨", "_trend": "",
         "_items": [
             _it("CVE-0000-00000 · Ejemplo Vendor Producto: ejecución remota de código (entrada de ejemplo)",
                 "CISA KEV", "Vulnerabilidad explotada activamente en un componente de red; existe parche del fabricante.",
                 "Si tienes este componente expuesto en algún cliente, es prioridad de parcheo hoy.",
                 "seguridad", ransomware=True),
             _it("Ejemplo: Paquete malicioso en un registro público roba credenciales de CI",
                 "BleepingComputer", "Campaña de supply chain dirigida a pipelines que instalan dependencias sin lockfile.",
                 "Revisa que tus pipelines fijen versiones y usen tokens de mínimo privilegio.", "seguridad", buzz=1),
         ]},
        {"title": "Cloud: AWS · Azure · GCP", "emoji": "☁️", "_trend": "",
         "_items": [
             _it("Ejemplo: Un proveedor cloud anuncia nuevo tier de almacenamiento para cargas de analítica",
                 "AWS News Blog", "Nuevo tipo de almacenamiento optimizado para tablas en formato abierto con menor latencia.",
                 "Afecta decisiones de diseño de lakehouse que estés evaluando.", "lanzamiento"),
         ]},
        {"title": "GenAI, LLMOps y Sistemas Agénticos", "emoji": "🤖",
         "_trend": "Más equipos publican cómo evalúan agentes en producción en vez de solo demos.",
         "_items": [
             _it("Ejemplo: Guía práctica para evaluar agentes con trazas de OpenTelemetry",
                 "Simon Willison", "Recorrido de cómo instrumentar llamadas a herramientas de un agente y medir regresiones.",
                 "Une observabilidad (que ya dominas) con LLMOps: puente natural para aprender.", "practico"),
             _it("Ejemplo: Servidor de inferencia open source añade soporte de decodificación especulativa",
                 "vllm-project/vllm", "Release mayor con mejoras de throughput en GPUs de gama media.",
                 "Relevante si sirves modelos propios en tu clúster.", "lanzamiento"),
         ]},
        {"title": "Lenguajes: Go · Python · Rust · Elixir · Zig · Erlang · TS · Bash", "emoji": "💻", "_trend": "",
         "_items": [
             _it("Ejemplo: Propuesta aceptada para iteradores genéricos en la librería estándar",
                 "Go Blog", "Cambio de lenguaje que llegará en la próxima versión menor.",
                 "Cambia patrones idiomáticos que usas a diario en Go.", "radar"),
         ]},
        {"title": "Proyectos en tendencia (GitHub)", "emoji": "⭐", "_trend": "",
         "_items": [
             _it("Ejemplo: ejemplo-org/k8s-cost-lens", "GitHub Trending · go",
                 "CLI que estima el costo por namespace leyendo métricas de Prometheus.",
                 "Útil para conversaciones de FinOps con clientes.", "practico"),
         ]},
    ]
    titulares = [
        "Ejemplo: una vulnerabilidad de red explotada activamente entra al catálogo KEV; hay parche disponible.",
        "Ejemplo: la comunidad debate cuándo Kubernetes deja de compensar su complejidad.",
        "Ejemplo: los equipos de LLMOps convergen en OpenTelemetry para evaluar agentes.",
    ]
    explorar = {"title": secs[3]["_items"][0].title, "url": secs[3]["_items"][0].url, "source": "Simon Willison",
                "por_que": "Conecta algo que ya dominas (observabilidad) con el tema que más se mueve (agentes).",
                "plan": "Lee la guía (10 min) e instrumenta un script de prueba con el SDK de OTel en Python (10 min)."}
    return secs, titulares, explorar
