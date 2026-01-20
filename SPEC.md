# Twitter Bookmark Processor

> **Versao:** 2.2 | **Ultima atualizacao:** 2026-01-20

## Objetivo

Sistema automatico para processar bookmarks do Twitter/X, extrair conhecimento e gerar notas Obsidian.

## Requisitos

- **Deteccao hibrida:** Polling (2 min) + iOS Share Sheet (imediato)
- **Tipos de conteudo:** Videos | Threads | Links | Tweets simples
- **Output:** `/workspace/notes/twitter/` (Obsidian-compatible)
- **Backlog:** ~500-2000 bookmarks existentes
- **Prioridade:** Qualidade > Custo

---

## Modelo de Dados

Inspirado no dataclass `Tweet` do `references/twitter_reader.py`, mas com implementacao propria:

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

class ContentType(str, Enum):
    VIDEO = "video"      # video_urls preenchido OU youtube.com em links
    THREAD = "thread"    # conversation_id detectado OU reply_count > 0 do mesmo autor
    LINK = "link"        # links externos (nao twitter/youtube)
    TWEET = "tweet"      # texto simples ou com imagens

class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"

@dataclass
class Bookmark:
    # Identificacao
    id: str                              # Tweet ID
    url: str                             # URL completa

    # Conteudo
    text: str
    author_username: str
    author_name: str = ""
    author_id: Optional[str] = None      # Para deteccao de thread (reply chain)
    created_at: str = ""

    # Campos para deteccao de thread
    conversation_id: Optional[str] = None       # Se != id, faz parte de thread
    in_reply_to_user_id: Optional[str] = None   # Se == author_id, e reply do mesmo autor

    # Classificacao (detectado automaticamente)
    content_type: ContentType = ContentType.TWEET
    media_urls: List[str] = field(default_factory=list)
    video_urls: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    is_thread: bool = False              # Pode ser setado por heuristica

    # Metadados de processamento
    bookmarked_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    status: ProcessingStatus = ProcessingStatus.PENDING
    error_count: int = 0
    last_error: Optional[str] = None
    output_path: Optional[str] = None
```

---

## Regras de Classificacao

**Abordagem:** Implementacao propria inspirada nos padroes do `references/twitter_reader.py`.

**Nota:** O fallback ThreadReaderApp do `twitter_reader.py` (que usa `requests`) NAO sera utilizado neste projeto. Usamos apenas `httpx` para HTTP.

| Tipo | Condicao | Deteccao |
|------|----------|----------|
| VIDEO | Video nativo OU YouTube em links | `media.type == 'video'` OU dominio youtube/youtu.be |
| THREAD | Tweet faz parte de thread | `conversation_id` != `tweet_id` OU heuristica de reply chain |
| LINK | Links externos (nao twitter/youtube) | Parse de `entities.urls` |
| TWEET | Nenhuma das acima | Default |

### Deteccao de Threads (importante)

Threads nao vem com flag pronta - precisam ser detectadas. Estrategias:

1. **Via API/bird:** Verificar se `conversation_id` difere do `tweet_id`
2. **Via Twillot export:** Campo `in_reply_to_status_id` do mesmo autor
3. **Heuristica:** Numeros no texto como "1/", "2/", "(thread)" no inicio

```python
import re

def classify(bookmark: Bookmark) -> ContentType:
    # 1. Video nativo do Twitter
    if bookmark.video_urls:
        return ContentType.VIDEO

    # 2. Video externo (YouTube)
    youtube_domains = ["youtube.com", "youtu.be", "vimeo.com"]
    if any(any(d in link for d in youtube_domains) for link in bookmark.links):
        return ContentType.VIDEO

    # 3. Thread (multiplas estrategias)
    if _is_thread(bookmark):
        return ContentType.THREAD

    # 4. Link externo
    twitter_domains = ["twitter.com", "x.com", "t.co"]
    external_links = [l for l in bookmark.links if not any(d in l for d in twitter_domains)]
    if external_links:
        return ContentType.LINK

    # 5. Tweet simples
    return ContentType.TWEET

def _is_thread(bookmark: Bookmark) -> bool:
    """Detecta se bookmark e parte de thread."""
    # Estrategia 1: conversation_id diferente do tweet_id
    if bookmark.conversation_id and bookmark.conversation_id != bookmark.id:
        return True

    # Estrategia 2: reply do mesmo autor
    if bookmark.in_reply_to_user_id and bookmark.in_reply_to_user_id == bookmark.author_id:
        return True

    # Estrategia 3: heuristica textual
    thread_patterns = [r'^\d+[/\.]', r'\(thread\)', r'🧵']
    if any(re.search(p, bookmark.text, re.I) for p in thread_patterns):
        return True

    return False
```

---

## Arquitetura

```
/workspace/twitter-bookmark-processor/
├── src/
│   ├── main.py                  # Polling daemon
│   ├── webhook_server.py        # iOS Share Sheet endpoint (porta 8766)
│   ├── core/
│   │   ├── bookmark.py          # Data model (Bookmark, ContentType)
│   │   ├── classifier.py        # Implementacao propria (inspirada em twitter_reader.py)
│   │   └── state_manager.py     # JSON + file lock (fcntl)
│   ├── sources/
│   │   └── twillot_reader.py    # Parse Twillot JSON export
│   ├── processors/
│   │   ├── base.py              # BaseProcessor interface
│   │   ├── video_processor.py   # Delega para /youtube-video skill
│   │   ├── thread_processor.py  # Delega para /twitter skill
│   │   ├── link_processor.py    # LLM knowledge extraction
│   │   └── tweet_processor.py   # Tweet simples + categorizacao
│   └── output/
│       └── obsidian_writer.py   # Gera notas .md com frontmatter
├── data/
│   ├── state.json               # Estado de processamento
│   ├── link_cache.json          # Cache de URLs processadas
│   └── backlog/                 # Twillot exports
├── tests/
│   ├── test_classifier.py
│   ├── test_twillot_reader.py
│   ├── test_state_manager.py
│   └── test_obsidian_writer.py
└── setup-macos.sh               # Configura launchd services
```

**Venv persistente:** `/workspace/.mcp-tools/twitter-processor/venv/`

---

## Politica de Concorrencia

| Parametro | Valor | Justificativa |
|-----------|-------|---------------|
| Workers | 4 | Balanco entre throughput e recursos |
| Rate limit (YouTube) | 1 req/s | Limite da skill existente |
| Rate limit (Twitter/bird) | 2 req/s | Evitar bloqueio |
| Rate limit (LLM) | 5 req/s | Custo aceitavel |

**Nota:** Semaforo limita concorrencia, nao taxa. Para rate limiting real, usamos token bucket com sleep.

```python
import asyncio
import time
from contextlib import asynccontextmanager

class RateLimiter:
    """Rate limiter real com token bucket por tipo de conteudo."""

    def __init__(self, max_workers: int = 4):
        self.semaphore = asyncio.Semaphore(max_workers)
        # Intervalo minimo entre requests por tipo (em segundos)
        self.intervals = {
            ContentType.VIDEO: 1.0,    # 1 req/s
            ContentType.THREAD: 0.5,   # 2 req/s
            ContentType.LINK: 0.2,     # 5 req/s
            ContentType.TWEET: 0.1,    # 10 req/s
        }
        self.last_request: dict[ContentType, float] = {}
        self._locks: dict[ContentType, asyncio.Lock] = {
            t: asyncio.Lock() for t in ContentType
        }

    @asynccontextmanager
    async def acquire(self, content_type: ContentType):
        """Adquire slot respeitando rate limit real."""
        async with self.semaphore:
            async with self._locks[content_type]:
                # Calcula tempo de espera
                now = time.monotonic()
                last = self.last_request.get(content_type, 0)
                wait_time = max(0, self.intervals[content_type] - (now - last))

                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                self.last_request[content_type] = time.monotonic()
                yield
```

---

## Politica de Erros e Retry

| Erro | Acao | Max retries |
|------|------|-------------|
| Timeout | Retry com backoff exponencial | 3 |
| Rate limit (429) | Espera + retry | 5 |
| Conteudo deletado | Marca como `error`, nao retenta | 0 |
| Erro de parse | Retry uma vez, depois marca `error` | 1 |
| Erro de LLM | Retry com backoff | 3 |

```python
import asyncio
from typing import Callable

async def with_retry(
    func: Callable,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0
):
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except RateLimitError:
            delay = min(base_delay * (2 ** attempt), max_delay)
            await asyncio.sleep(delay)
        except ContentDeletedError:
            raise  # Nao retenta
        except Exception as e:
            if attempt == max_retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            await asyncio.sleep(delay)
```

---

## State Manager

**Formato:** JSON com file lock (`fcntl`) para escrita atomica.

```python
import fcntl
import json
from pathlib import Path
from datetime import datetime

STATE_FILE = Path("data/state.json")

class StateManager:
    def __init__(self):
        self._lock_file = STATE_FILE.with_suffix(".lock")

    def _atomic_write(self, data: dict):
        """Escrita atomica com lock."""
        with open(self._lock_file, "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                # Write to temp, then rename (atomic)
                temp = STATE_FILE.with_suffix(".tmp")
                temp.write_text(json.dumps(data, indent=2, default=str))
                temp.rename(STATE_FILE)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def mark_processed(self, bookmark_id: str, status: ProcessingStatus,
                       output_path: str = None, error: str = None):
        state = self._load()
        state["bookmarks"][bookmark_id] = {
            "status": status.value,
            "processed_at": datetime.now().isoformat(),
            "output_path": output_path,
            "error": error
        }
        state["stats"][status.value] = state["stats"].get(status.value, 0) + 1
        self._atomic_write(state)
```

**Nota de evolucao:** Se volume crescer para milhares/dia, migrar para SQLite.

---

## Processamento de Links (LLM)

**Principio:** Qualidade > Custo. LLM e o padrao para extracao.

**Estrategia:**
1. Cache por URL (hash SHA256) com TTL de 30 dias
2. LLM (Claude Haiku) como padrao para extracao
3. Fallback para metadata basico se LLM falhar

```python
import hashlib
from pathlib import Path
from datetime import datetime, timedelta

CACHE_FILE = Path("data/link_cache.json")
CACHE_TTL_DAYS = 30

class LinkProcessor:
    def __init__(self, llm_client):
        self.llm = llm_client
        self.cache = self._load_cache()

    def _url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _is_cache_valid(self, cache_entry: dict) -> bool:
        """Verifica se entrada do cache ainda e valida (TTL)."""
        cached_at = datetime.fromisoformat(cache_entry.get("cached_at", "2000-01-01"))
        return datetime.now() - cached_at < timedelta(days=CACHE_TTL_DAYS)

    async def process(self, bookmark: Bookmark) -> ProcessResult:
        for link in bookmark.links:
            cache_key = self._url_hash(link)

            # 1. Check cache (com TTL)
            if cache_key in self.cache and self._is_cache_valid(self.cache[cache_key]):
                return self.cache[cache_key]["data"]

            # 2. Fetch content
            content = await self._fetch_content(link)

            # 3. LLM extraction (qualidade > custo)
            extracted = await self.llm.extract_knowledge(
                content=content,
                context=f"Tweet de @{bookmark.author_username}: {bookmark.text}",
                prompt="""Extraia:
                - Titulo
                - TL;DR (2-3 frases)
                - Key points (3-5 bullets)
                - Tags relevantes
                """
            )

            # 4. Cache result (com timestamp)
            self.cache[cache_key] = {
                "data": extracted,
                "cached_at": datetime.now().isoformat()
            }
            self._save_cache()

            return extracted
```

---

## Webhook (Seguranca)

**Autenticacao:** Bearer token no header.

```python
import os
from http.server import BaseHTTPRequestHandler

WEBHOOK_TOKEN = os.environ.get("TWITTER_WEBHOOK_TOKEN", "")

class WebhookHandler(BaseHTTPRequestHandler):
    def _check_auth(self) -> bool:
        if not WEBHOOK_TOKEN:
            return True  # Dev mode
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {WEBHOOK_TOKEN}"

    def do_POST(self):
        if not self._check_auth():
            self._send_json({"error": "Unauthorized"}, 401)
            return
        # ... processo normal
```

**iOS Shortcut:** Adicionar header `Authorization: Bearer {token}`.

---

## Notificacoes

**Formato enriquecido:**

| Status | Exemplo |
|--------|---------|
| Sucesso | `"Thread (12 tweets) de @naval processada em 8s"` |
| Erro | `"Erro em video: timeout apos 5min"` |
| Sumario | `"Dia: 15 processados, 2 erros, 45s medio"` |

```python
def format_notification(bookmark: Bookmark, result: ProcessResult) -> str:
    type_emoji = {
        ContentType.VIDEO: "🎥",
        ContentType.THREAD: "🧵",
        ContentType.LINK: "🔗",
        ContentType.TWEET: "💬"
    }

    if result.success:
        return (
            f"{type_emoji[bookmark.content_type]} "
            f"{bookmark.content_type.value.title()} de @{bookmark.author_username} "
            f"processado em {result.duration_ms/1000:.1f}s"
        )
    else:
        return f"❌ Erro em {bookmark.content_type.value}: {result.error[:50]}"
```

---

## Data Flow

### Polling (Background)
```
Twillot export -> data/backlog/ -> twillot_reader.py
     |
     v
classifier.py (implementação própria)
     |
     +--[VIDEO]---> video_processor.py --> /youtube-video skill
     |
     +--[THREAD]--> thread_processor.py --> /twitter skill
     |
     +--[LINK]----> link_processor.py --> LLM (Claude Haiku) + cache
     |
     +--[TWEET]---> tweet_processor.py --> extracao basica
     |
     v
obsidian_writer.py -> /workspace/notes/twitter/{type}/
     |
     v
notify (Telegram) - formato enriquecido
```

### Share Sheet (Imediato)
```
iOS Share -> Shortcut -> POST http://100.66.201.114:8766/process
                         Header: Authorization: Bearer {token}
     |
     v
webhook_server.py -> 202 Accepted + {id, status}
     |
     v
(mesmo flow acima, em thread separada)
```

---

## Integracao com Skills Existentes

| Tipo | Script | Chamada |
|------|--------|---------|
| Video | `~/.claude/skills/youtube-video/scripts/youtube_processor.py` | `python3 {script} {url} --note -o {output_dir}` |
| Thread | `~/.claude/skills/twitter/scripts/twitter_reader.py` | `python3 {script} {url} --thread --json` |
| Classificacao | N/A (implementação própria) | Inspirado na estrutura do twitter_reader.py |

---

## Output Format (Obsidian)

```markdown
---
schema_version: 1
id: "1234567890"
title: "Thread Title"
source: https://x.com/user/status/123
author: "@username"
type: thread|video|link|tweet
date_created: 2025-01-15
date_bookmarked: 2025-01-20
date_processed: 2025-01-20T14:30:00
tags: [twitter, topic1, topic2]
---

# Title

## TL;DR
...

## Key Points
- Point 1
- Point 2

## Content
...

---
*Processado por twitter-bookmark-processor v2.0*
```

---

## Observabilidade

**Log estruturado (JSON):**

```python
import json
import logging
from datetime import datetime

class StructuredLogger:
    def log(self, bookmark_id: str, event: str, **kwargs):
        entry = {
            "ts": datetime.now().isoformat(),
            "bookmark_id": bookmark_id,
            "event": event,
            **kwargs
        }
        print(json.dumps(entry))

# Uso:
logger.log("123", "process_start", type="thread")
logger.log("123", "process_done", duration_ms=8200, status="done")
logger.log("123", "process_error", error="timeout", retry=2)
```

**Metricas (v2):** Prometheus/StatsD se necessario no futuro.

---

## Implementacao (Sequencia)

### Fase 1: Core
1. Criar estrutura do projeto
2. Setup venv em `/workspace/.mcp-tools/twitter-processor/`
3. `bookmark.py` - Data model (Bookmark, ContentType, ProcessingStatus)
4. `state_manager.py` - JSON + file lock + atomic write
5. `twillot_reader.py` - Parse JSON export do Twillot

### Fase 2: Processors
6. `classifier.py` - Implementação própria (inspirada em twitter_reader.py)
7. `video_processor.py` - Integra com skill existente
8. `thread_processor.py` - Integra com skill existente
9. `link_processor.py` - LLM extraction + cache
10. `tweet_processor.py` - Extracao basica

### Fase 3: Output & Webhook
11. `obsidian_writer.py` - Templates Jinja2 por tipo
12. `webhook_server.py` - Com autenticacao Bearer token
13. `main.py` - Polling daemon com rate limiting

### Fase 4: Deploy
14. `setup-macos.sh` - launchd plists
15. iOS Shortcut "Process Tweet" (com header auth)
16. Testar com backlog real

---

## Dependencias

```
anthropic>=0.18.0
httpx>=0.27.0
beautifulsoup4>=4.12.0
jinja2>=3.1.0
python-dateutil>=2.8.0
```

**Nota:** Removido `requests` - usar apenas `httpx`.

---

## Testes

| Modulo | Escopo |
|--------|--------|
| `test_classifier.py` | Regras VIDEO/THREAD/LINK/TWEET |
| `test_twillot_reader.py` | Parse de exports JSON |
| `test_state_manager.py` | Concorrencia e atomic writes |
| `test_obsidian_writer.py` | Formato de output |
| `test_link_processor.py` | Cache e fallback |

---

## Verificacao

1. **Unit tests:** `python3 -m pytest tests/ -v`
2. **Manual - Polling:**
   - Drop um export Twillot em `data/backlog/`
   - `python3 src/main.py --once`
   - Verificar nota gerada em `/workspace/notes/twitter/`
   - Verificar logs estruturados
3. **Manual - Webhook:**
   - `TWITTER_WEBHOOK_TOKEN=test python3 src/webhook_server.py`
   - `curl -X POST http://localhost:8766/process -H "Authorization: Bearer test" -d '{"url":"https://x.com/naval/status/123"}'`
   - Verificar notificacao Telegram
4. **Backlog:** Processar ~10 bookmarks variados (video, thread, link, tweet)
5. **Concorrencia:** Processar 50 bookmarks simultaneos, verificar rate limiting

---

## Arquivos Criticos de Referencia

- `~/.claude/skills/twitter/scripts/twitter_reader.py` - **Tweet dataclass, _bird_to_tweet(), classificacao**
- `~/.claude/skills/youtube-video/scripts/youtube_processor.py` - Skill de video
- `/workspace/_scripts/yt-webhook/server.py` - Padrao do webhook
- `/workspace/_scripts/kb_processor.py` - Padrao de state management
