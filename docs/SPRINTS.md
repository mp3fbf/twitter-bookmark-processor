# Twitter Bookmark Processor - Sprint & Task Breakdown

> **Objetivo:** Decomposição exaustiva em sprints e tasks atômicos com validação clara.
> **Princípio:** Cada task é um commit isolado com testes. Cada sprint entrega software demoável.

---

## Risk Assessment

| Task | Risk Level | Reason |
|------|------------|--------|
| 0.2 (Skill Integration Discovery) | **High** | Skills run in own venvs; `bird` CLI on host only |
| 3.1-3.4 (Skill Integration) | **High** | Subprocess invocation across environments |
| 4.4 (LLM Extraction) | **High** | Output parsing fragile; cost for large backlog |
| 1.5-1.6 (Twillot Parser) | **Medium** | Format undocumented; may change |
| 5.4 (Concurrent Processing) | **Medium** | Race conditions despite atomic writes |
| 2.2 (Thread Detection) | **Medium** | Heuristics have false positives |

---

## Sprint 0: Discovery Phase (Pre-requisite)

**Goal:** Verify assumptions before writing code.
**Demo:** Sample fixtures created + skill integration method documented.

### Task 0.1: Twillot Format Discovery
**Scope:** Export 10+ real bookmarks from Twillot, document actual JSON schema.
**Output:**
- `tests/fixtures/twillot_sample.json` (anonymized real data)
- `docs/twillot-schema.md` (field documentation)

**Validation:**
1. Export contains at least: VIDEO, THREAD, LINK, TWEET examples
2. Fields documented: `id`, `text`, `author`, `media`, `urls`, `conversation_id` (if exists)
3. Edge cases captured: deleted tweet reference, quote tweet, retweet

---

### Task 0.2: Skill Integration Discovery
**Scope:** Analyze existing skill implementations and determine integration method.
**Files to analyze:**
- `/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py`
- `/home/claude/.claude/skills/twitter/scripts/twitter_reader.py`

**Questions to answer:**
1. Can skills be called from Docker container? (bird CLI on host?)
2. What environment variables required? (GOOGLE_API_KEY, etc.)
3. What is the exact command and output format?
4. Can we import as library or must subprocess?

**Output:** `docs/skill-integration.md` with:
- Command examples
- Environment requirements
- Output format documentation
- Decision: container vs host execution

**Validation:** Successfully call each skill manually with sample URL

---

## Sprint 1: Foundation - Project Structure & Data Models

**Goal:** Estabelecer estrutura do projeto, data models e estado persistente.
**Demo:** `python -m pytest tests/` passa + carregar export Twillot e mostrar bookmarks parseados.

### Task 1.1: Project Scaffolding
**Scope:** Criar estrutura de diretórios e arquivos iniciais.
**Files:**
- `src/__init__.py`
- `src/core/__init__.py`
- `src/sources/__init__.py`
- `src/processors/__init__.py`
- `src/output/__init__.py`
- `tests/__init__.py`
- `data/.gitkeep`
- `data/backlog/.gitkeep`
- `requirements.txt`
- `pyproject.toml` (pytest config)

**Validation:**
```bash
# Estrutura existe
ls -la src/core/ src/sources/ src/processors/ src/output/ tests/ data/
# Requirements instalável
pip install -r requirements.txt
```

---

### Task 1.2: Bookmark Data Model
**Scope:** Implementar `Bookmark`, `ContentType`, `ProcessingStatus` dataclasses.
**File:** `src/core/bookmark.py`
**Tests:** `tests/test_bookmark.py`

**Test Cases:**
1. `test_bookmark_creation` - Criar bookmark com campos mínimos
2. `test_bookmark_with_all_fields` - Criar com todos os campos opcionais
3. `test_content_type_enum_values` - VIDEO, THREAD, LINK, TWEET
4. `test_processing_status_enum_values` - PENDING, PROCESSING, DONE, ERROR
5. `test_bookmark_default_values` - Verificar defaults corretos

**Validation:**
```bash
python -m pytest tests/test_bookmark.py -v
```

---

### Task 1.3: State Manager - Core
**Scope:** Implementar `StateManager` com load/save JSON.
**File:** `src/core/state_manager.py`
**Tests:** `tests/test_state_manager.py`

**Test Cases:**
1. `test_state_manager_creates_file_if_missing` - Primeiro uso cria arquivo
2. `test_state_manager_load_existing` - Carrega estado existente
3. `test_state_manager_is_processed` - Verifica se ID já foi processado
4. `test_state_manager_mark_processed` - Marca como processado com status

**Validation:**
```bash
python -m pytest tests/test_state_manager.py -v
```

---

### Task 1.4: State Manager - Atomic Writes
**Scope:** Adicionar file lock (`fcntl`) e atomic write (temp + rename).
**File:** `src/core/state_manager.py` (extend)
**Tests:** `tests/test_state_manager.py` (extend)

**Test Cases:**
1. `test_atomic_write_creates_temp_first` - Verifica padrão temp → rename
2. `test_concurrent_writes_no_corruption` - Simula 10 escritas paralelas
3. `test_lock_file_created` - `.lock` file existe durante escrita

**Validation:**
```bash
python -m pytest tests/test_state_manager.py::test_concurrent_writes_no_corruption -v
```

---

### Task 1.5: Twillot Reader - Parse Export
**Scope:** Parser para JSON export do Twillot.
**File:** `src/sources/twillot_reader.py`
**Tests:** `tests/test_twillot_reader.py`
**Fixtures:** `tests/fixtures/twillot_sample.json` (mock data)

**Test Cases:**
1. `test_parse_single_bookmark` - Um bookmark simples
2. `test_parse_bookmark_with_media` - Bookmark com imagens
3. `test_parse_bookmark_with_video` - Bookmark com video nativo
4. `test_parse_bookmark_with_links` - Bookmark com URLs externos
5. `test_parse_empty_export` - Export vazio retorna lista vazia
6. `test_parse_invalid_json_raises` - JSON inválido levanta exceção

**Validation:**
```bash
python -m pytest tests/test_twillot_reader.py -v
```

---

### Task 1.6: Twillot Reader - Thread Detection Fields
**Scope:** Extrair `conversation_id`, `in_reply_to_user_id`, `author_id` do export.
**File:** `src/sources/twillot_reader.py` (extend)
**Tests:** `tests/test_twillot_reader.py` (extend)

**Test Cases:**
1. `test_parse_conversation_id` - Extrai conversation_id se presente
2. `test_parse_in_reply_to_user_id` - Extrai campo de reply
3. `test_parse_author_id` - Extrai ID do autor

**Validation:**
```bash
python -m pytest tests/test_twillot_reader.py -v
```

---

### Task 1.7: Configuration Manager
**Scope:** Centralized configuration loading from env vars with defaults.
**File:** `src/core/config.py`
**Tests:** `tests/test_config.py`

**Configuration Keys:**
- `ANTHROPIC_API_KEY` (required for LLM)
- `TWITTER_WEBHOOK_TOKEN` (optional, for auth)
- `TWITTER_OUTPUT_DIR` (default: /workspace/notes/twitter/)
- `TWITTER_STATE_FILE` (default: data/state.json)
- `TWITTER_CACHE_FILE` (default: data/link_cache.json)
- `TWITTER_RATE_LIMIT_VIDEO` (default: 1.0)
- `TWITTER_RATE_LIMIT_THREAD` (default: 0.5)
- `TWITTER_RATE_LIMIT_LINK` (default: 0.2)

**Test Cases:**
1. `test_config_loads_from_env` - Env vars loaded correctly
2. `test_config_uses_defaults` - Missing env → default value
3. `test_config_validates_required` - Missing ANTHROPIC_API_KEY → error
4. `test_config_validates_paths` - Invalid path → error
5. `test_config_singleton` - Same instance returned

**Validation:**
```bash
python -m pytest tests/test_config.py -v
```

---

## Sprint 2: Classification & Tweet Processing

**Goal:** Classificar bookmarks por tipo e processar tweets simples.
**Demo:** Carregar export → classificar cada um → gerar nota Obsidian para tweet simples.

### Task 2.0: Exception Types
**Scope:** Define custom exceptions for error handling.
**File:** `src/core/exceptions.py`
**Tests:** `tests/test_exceptions.py`

**Exception Classes:**
```python
class ProcessorError(Exception): """Base class for processor errors."""
class RateLimitError(ProcessorError): """API rate limit hit."""
class ContentDeletedError(ProcessorError): """Tweet/content was deleted."""
class SkillError(ProcessorError): """External skill failed."""
class ParseError(ProcessorError): """Failed to parse content."""
class ConfigurationError(Exception): """Invalid configuration."""
```

**Test Cases:**
1. `test_exception_hierarchy` - All inherit from ProcessorError
2. `test_exception_messages` - Custom messages preserved
3. `test_exception_retryable_flag` - RateLimitError is retryable, ContentDeletedError is not

**Validation:**
```bash
python -m pytest tests/test_exceptions.py -v
```

---

### Task 2.1: Classifier - Video Detection (YouTube-only)
**Scope:** Detectar VIDEO (video nativo ou YouTube em links).
**File:** `src/core/classifier.py`
**Tests:** `tests/test_classifier.py`

**Test Cases:**
1. `test_classify_video_native` - `video_urls` preenchido → VIDEO
2. `test_classify_video_youtube_link` - youtube.com em links → VIDEO
3. `test_classify_video_youtu_be` - youtu.be em links → VIDEO
4. `test_classify_video_unsupported_logs_warning` - vimeo.com → VIDEO but logs "unsupported platform"

**Note:** Only YouTube is fully supported. Other video platforms will be classified as VIDEO but processing will return basic metadata only.

**Validation:**
```bash
python -m pytest tests/test_classifier.py::test_classify_video* -v
```

---

### Task 2.2: Classifier - Thread Detection
**Scope:** Detectar THREAD via conversation_id, reply chain, heurísticas.
**File:** `src/core/classifier.py` (extend)
**Tests:** `tests/test_classifier.py` (extend)

**Detection Strategy (priority order):**
1. `conversation_id != id` (definitive)
2. `in_reply_to_user_id == author_id` (definitive - reply chain)
3. Heuristics (require 2+ signals):
   - `^\d+[/.]` pattern at start
   - 🧵 emoji
   - "(thread)" word

**Test Cases - Positive:**
1. `test_classify_thread_by_conversation_id` - conversation_id != id → THREAD
2. `test_classify_thread_by_reply_chain` - in_reply_to_user_id == author_id → THREAD
3. `test_classify_thread_by_number_and_emoji` - "1/" + 🧵 → THREAD (multiple signals)
4. `test_classify_thread_by_word_and_pattern` - "(thread)" + "1/" → THREAD

**Test Cases - Negative (Avoid False Positives):**
5. `test_classify_rejects_numbered_list` - "1. First point..." → NOT THREAD (single signal)
6. `test_classify_rejects_thread_mention` - "Great thread by @user" → NOT THREAD
7. `test_classify_rejects_emoji_alone` - Random 🧵 without pattern → NOT THREAD
8. `test_classify_rejects_reply_to_other_user` - in_reply_to_user_id != author_id → NOT THREAD

**Validation:**
```bash
python -m pytest tests/test_classifier.py::test_classify_thread* -v
```

---

### Task 2.3: Classifier - Link & Tweet Detection
**Scope:** Detectar LINK (externo) e TWEET (default).
**File:** `src/core/classifier.py` (extend)
**Tests:** `tests/test_classifier.py` (extend)

**Test Cases:**
1. `test_classify_link_external` - Links não-twitter/youtube → LINK
2. `test_classify_link_ignores_twitter` - twitter.com/x.com ignorados
3. `test_classify_link_ignores_t_co` - t.co ignorados
4. `test_classify_tweet_default` - Sem video/thread/link → TWEET
5. `test_classify_tweet_with_images_only` - Imagens sem link → TWEET

**Validation:**
```bash
python -m pytest tests/test_classifier.py -v
```

---

### Task 2.4: Base Processor Interface
**Scope:** Criar `BaseProcessor` ABC e `ProcessResult` dataclass.
**File:** `src/processors/base.py`
**Tests:** `tests/test_processors_base.py`

**Interface:**
```python
class BaseProcessor(ABC):
    @abstractmethod
    async def process(self, bookmark: Bookmark) -> ProcessResult

@dataclass
class ProcessResult:
    success: bool
    content: Optional[str] = None
    title: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0
```

**Test Cases:**
1. `test_process_result_success` - Criar resultado de sucesso
2. `test_process_result_error` - Criar resultado de erro
3. `test_base_processor_is_abstract` - Não pode instanciar diretamente

**Validation:**
```bash
python -m pytest tests/test_processors_base.py -v
```

---

### Task 2.5: Tweet Processor
**Scope:** Processar tweets simples (extração básica).
**File:** `src/processors/tweet_processor.py`
**Tests:** `tests/test_tweet_processor.py`

**Test Cases:**
1. `test_process_simple_tweet` - Tweet só texto
2. `test_process_tweet_with_images` - Tweet com imagens
3. `test_process_tweet_extracts_title` - Primeiras palavras como título
4. `test_process_tweet_extracts_hashtags_as_tags` - #tags → tags
5. `test_process_tweet_returns_duration` - duration_ms preenchido

**Validation:**
```bash
python -m pytest tests/test_tweet_processor.py -v
```

---

### Task 2.6: Obsidian Writer - Core
**Scope:** Gerar markdown com YAML frontmatter.
**File:** `src/output/obsidian_writer.py`
**Tests:** `tests/test_obsidian_writer.py`

**Test Cases:**
1. `test_write_creates_file` - Arquivo .md criado no path correto
2. `test_write_yaml_frontmatter` - Frontmatter válido com campos obrigatórios
3. `test_write_content_body` - Corpo do markdown presente
4. `test_write_escapes_special_chars` - Caracteres especiais escapados
5. `test_write_returns_output_path` - Retorna path do arquivo criado

**Validation:**
```bash
python -m pytest tests/test_obsidian_writer.py -v
```

---

### Task 2.7: Obsidian Writer - Templates
**Scope:** Templates Jinja2 por tipo de conteúdo.
**Files:**
- `src/output/obsidian_writer.py` (extend)
- `src/output/templates/tweet.md.j2`
- `src/output/templates/base.md.j2`
**Tests:** `tests/test_obsidian_writer.py` (extend)

**Test Cases:**
1. `test_tweet_template_structure` - Template tweet tem TL;DR, Content
2. `test_template_includes_footer` - Footer com versão do processor

**Validation:**
```bash
python -m pytest tests/test_obsidian_writer.py -v
```

---

### Task 2.8: End-to-End Tweet Processing
**Scope:** Integrar twillot_reader → classifier → tweet_processor → obsidian_writer.
**File:** `src/core/pipeline.py`
**Tests:** `tests/test_pipeline.py`

**Test Cases:**
1. `test_pipeline_tweet_e2e` - Export com tweet → nota .md gerada
2. `test_pipeline_updates_state` - State manager atualizado após processamento

**Validation:**
```bash
python -m pytest tests/test_pipeline.py::test_pipeline_tweet_e2e -v
# Manual: verificar nota em /tmp/test_notes/
```

---

## Sprint 3: External Skill Integration (Video & Thread)

**Goal:** Delegar para skills existentes de YouTube e Twitter.
**Demo:** Processar bookmark de vídeo YouTube → nota com transcrição. Processar thread → nota completa.

### Task 3.1: Video Processor - Skill Integration
**Scope:** Chamar `/youtube-video` skill via subprocess.
**File:** `src/processors/video_processor.py`
**Tests:** `tests/test_video_processor.py`

**Test Cases:**
1. `test_video_processor_calls_skill` - subprocess.run chamado com args corretos
2. `test_video_processor_parses_output` - Output da skill parseado
3. `test_video_processor_handles_timeout` - Timeout → erro gracioso
4. `test_video_processor_handles_skill_error` - Exit code != 0 → erro

**Validation:**
```bash
python -m pytest tests/test_video_processor.py -v
# Integration: processar URL YouTube real (manual)
```

---

### Task 3.2: Video Processor - Output Handling
**Scope:** Converter output da skill para `ProcessResult`.
**File:** `src/processors/video_processor.py` (extend)
**Tests:** `tests/test_video_processor.py` (extend)

**Test Cases:**
1. `test_video_output_extracts_title` - Título do vídeo extraído
2. `test_video_output_extracts_content` - Transcrição/resumo extraído
3. `test_video_output_finds_generated_file` - Localiza .md gerado pela skill

**Validation:**
```bash
python -m pytest tests/test_video_processor.py -v
```

---

### Task 3.3: Thread Processor - Skill Integration
**Scope:** Chamar `/twitter` skill via subprocess.
**File:** `src/processors/thread_processor.py`
**Tests:** `tests/test_thread_processor.py`

**Test Cases:**
1. `test_thread_processor_calls_skill` - subprocess.run com --thread --json
2. `test_thread_processor_parses_json` - Output JSON parseado
3. `test_thread_processor_handles_deleted_thread` - Thread deletada → erro
4. `test_thread_processor_handles_timeout` - Timeout → erro gracioso

**Validation:**
```bash
python -m pytest tests/test_thread_processor.py -v
```

---

### Task 3.4: Thread Processor - Content Formatting
**Scope:** Formatar thread em markdown estruturado.
**File:** `src/processors/thread_processor.py` (extend)
**Tests:** `tests/test_thread_processor.py` (extend)

**Test Cases:**
1. `test_thread_formats_multiple_tweets` - Cada tweet numerado
2. `test_thread_includes_media` - Imagens/links inclusos
3. `test_thread_extracts_key_points` - Pontos principais extraídos

**Validation:**
```bash
python -m pytest tests/test_thread_processor.py -v
```

---

### Task 3.5: Thread Template
**Scope:** Template Jinja2 específico para threads.
**Files:**
- `src/output/templates/thread.md.j2`
- `src/output/obsidian_writer.py` (extend)
**Tests:** `tests/test_obsidian_writer.py` (extend)

**Test Cases:**
1. `test_thread_template_has_tweet_count` - "Thread (N tweets)"
2. `test_thread_template_numbers_tweets` - Tweets numerados
3. `test_thread_template_has_key_points` - Seção Key Points

**Validation:**
```bash
python -m pytest tests/test_obsidian_writer.py::test_thread* -v
```

---

### Task 3.6: Video Template
**Scope:** Template Jinja2 específico para vídeos.
**Files:**
- `src/output/templates/video.md.j2`
- `src/output/obsidian_writer.py` (extend)
**Tests:** `tests/test_obsidian_writer.py` (extend)

**Test Cases:**
1. `test_video_template_has_duration` - Duração do vídeo
2. `test_video_template_has_transcript_section` - Seção de transcrição
3. `test_video_template_embeds_thumbnail` - Thumbnail como imagem

**Validation:**
```bash
python -m pytest tests/test_obsidian_writer.py::test_video* -v
```

---

### Task 3.7: Pipeline - Video & Thread Support
**Scope:** Integrar video_processor e thread_processor no pipeline.
**File:** `src/core/pipeline.py` (extend)
**Tests:** `tests/test_pipeline.py` (extend)

**Test Cases:**
1. `test_pipeline_video_e2e` - Export com video → skill chamada → nota
2. `test_pipeline_thread_e2e` - Export com thread → skill chamada → nota
3. `test_pipeline_routes_correctly` - VIDEO/THREAD/TWEET para processor correto

**Validation:**
```bash
python -m pytest tests/test_pipeline.py -v
```

---

## Sprint 4: Link Processing & LLM Integration

**Goal:** Extrair conhecimento de links externos usando LLM.
**Demo:** Processar bookmark com artigo → nota com TL;DR, key points, tags gerados por LLM.

### Task 4.1: HTTP Client Setup
**Scope:** Configurar httpx client com timeouts e retries.
**File:** `src/core/http_client.py`
**Tests:** `tests/test_http_client.py`

**Test Cases:**
1. `test_client_default_timeout` - Timeout padrão configurado
2. `test_client_follows_redirects` - Segue redirects
3. `test_client_user_agent` - User-Agent customizado

**Validation:**
```bash
python -m pytest tests/test_http_client.py -v
```

---

### Task 4.2: Link Content Fetcher
**Scope:** Buscar e extrair texto de URLs.
**File:** `src/processors/link_processor.py`
**Tests:** `tests/test_link_processor.py`

**Test Cases:**
1. `test_fetch_html_extracts_text` - HTML → texto limpo
2. `test_fetch_handles_timeout` - Timeout → erro
3. `test_fetch_handles_404` - 404 → erro
4. `test_fetch_respects_robots_txt` - Opcional, skip se bloqueado

**Validation:**
```bash
python -m pytest tests/test_link_processor.py::test_fetch* -v
```

---

### Task 4.3: LLM Client Setup
**Scope:** Configurar cliente Anthropic (Claude Haiku).
**File:** `src/core/llm_client.py`
**Tests:** `tests/test_llm_client.py`

**Test Cases:**
1. `test_llm_client_requires_api_key` - Erro se ANTHROPIC_API_KEY não set
2. `test_llm_client_uses_haiku` - Model é claude-3-haiku
3. `test_llm_client_structured_output` - Retorna JSON parseável

**Validation:**
```bash
python -m pytest tests/test_llm_client.py -v
```

---

### Task 4.4: Link Processor - LLM Extraction
**Scope:** Usar LLM para extrair título, TL;DR, key points, tags.
**File:** `src/processors/link_processor.py` (extend)
**Tests:** `tests/test_link_processor.py` (extend)

**Test Cases:**
1. `test_extract_returns_title` - Título extraído
2. `test_extract_returns_tldr` - TL;DR presente (2-3 frases)
3. `test_extract_returns_key_points` - 3-5 bullets
4. `test_extract_returns_tags` - Tags relevantes

**Validation:**
```bash
python -m pytest tests/test_link_processor.py::test_extract* -v
```

---

### Task 4.5: Link Cache - Core
**Scope:** Cache de extrações por URL hash com TTL.
**File:** `src/core/link_cache.py`
**Tests:** `tests/test_link_cache.py`

**Test Cases:**
1. `test_cache_stores_by_url_hash` - SHA256[:16] como key
2. `test_cache_retrieves_valid_entry` - Entry válida retornada
3. `test_cache_misses_expired_entry` - Entry expirada → miss
4. `test_cache_ttl_30_days` - TTL de 30 dias

**Validation:**
```bash
python -m pytest tests/test_link_cache.py -v
```

---

### Task 4.6: Link Processor - Cache Integration
**Scope:** Integrar cache no link processor.
**File:** `src/processors/link_processor.py` (extend)
**Tests:** `tests/test_link_processor.py` (extend)

**Test Cases:**
1. `test_processor_checks_cache_first` - Cache hit → não chama LLM
2. `test_processor_caches_result` - Resultado salvo no cache
3. `test_processor_llm_on_cache_miss` - Cache miss → LLM chamado

**Validation:**
```bash
python -m pytest tests/test_link_processor.py -v
```

---

### Task 4.7: Link Template
**Scope:** Template Jinja2 específico para links/artigos.
**Files:**
- `src/output/templates/link.md.j2`
- `src/output/obsidian_writer.py` (extend)
**Tests:** `tests/test_obsidian_writer.py` (extend)

**Test Cases:**
1. `test_link_template_has_source_url` - URL original presente
2. `test_link_template_has_tldr` - Seção TL;DR
3. `test_link_template_has_key_points` - Seção Key Points
4. `test_link_template_has_original_context` - Tweet que compartilhou

**Validation:**
```bash
python -m pytest tests/test_obsidian_writer.py::test_link* -v
```

---

### Task 4.8: Pipeline - Link Support
**Scope:** Integrar link_processor no pipeline.
**File:** `src/core/pipeline.py` (extend)
**Tests:** `tests/test_pipeline.py` (extend)

**Test Cases:**
1. `test_pipeline_link_e2e` - Export com link → LLM extraction → nota
2. `test_pipeline_link_uses_cache` - Segunda vez usa cache

**Validation:**
```bash
python -m pytest tests/test_pipeline.py::test_pipeline_link* -v
```

---

## Sprint 5: Rate Limiting & Concurrency

**Goal:** Processar múltiplos bookmarks com rate limiting correto.
**Demo:** Processar 20 bookmarks variados com rate limits respeitados.

### Task 5.1: Rate Limiter - Core
**Scope:** Token bucket rate limiter por tipo de conteúdo.
**File:** `src/core/rate_limiter.py`
**Tests:** `tests/test_rate_limiter.py`

**Test Cases:**
1. `test_rate_limiter_respects_interval` - Espera entre requests
2. `test_rate_limiter_different_rates_per_type` - VIDEO=1/s, LINK=5/s
3. `test_rate_limiter_concurrent_acquisition` - Semáforo limita workers

**Validation:**
```bash
python -m pytest tests/test_rate_limiter.py -v
```

---

### Task 5.2: Rate Limiter - Async Context Manager
**Scope:** Implementar `async with rate_limiter.acquire(type)`.
**File:** `src/core/rate_limiter.py` (extend)
**Tests:** `tests/test_rate_limiter.py` (extend)

**Test Cases:**
1. `test_acquire_waits_if_needed` - Mede tempo de espera
2. `test_acquire_releases_after_exit` - Semáforo liberado
3. `test_acquire_tracks_last_request` - Timestamp atualizado

**Validation:**
```bash
python -m pytest tests/test_rate_limiter.py -v
```

---

### Task 5.3: Retry Logic
**Scope:** Decorator/função para retry com backoff exponencial.
**File:** `src/core/retry.py`
**Tests:** `tests/test_retry.py`

**Test Cases:**
1. `test_retry_succeeds_first_try` - Sem retry se sucesso
2. `test_retry_with_backoff` - Backoff exponencial
3. `test_retry_max_attempts` - Para após N tentativas
4. `test_retry_raises_on_content_deleted` - Não retenta certos erros

**Validation:**
```bash
python -m pytest tests/test_retry.py -v
```

---

### Task 5.4: Pipeline - Concurrent Processing
**Scope:** Processar múltiplos bookmarks em paralelo com rate limiting.
**File:** `src/core/pipeline.py` (extend)
**Tests:** `tests/test_pipeline.py` (extend)

**Test Cases:**
1. `test_pipeline_processes_batch` - 10 bookmarks processados
2. `test_pipeline_respects_rate_limits` - Timing verificado
3. `test_pipeline_handles_partial_failure` - 1 falha não para outros

**Validation:**
```bash
python -m pytest tests/test_pipeline.py::test_pipeline_processes_batch -v
```

---

### Task 5.5: Structured Logger
**Scope:** Logger JSON para observabilidade.
**File:** `src/core/logger.py`
**Tests:** `tests/test_logger.py`

**Test Cases:**
1. `test_logger_json_format` - Output é JSON válido
2. `test_logger_includes_timestamp` - Campo `ts` presente
3. `test_logger_includes_bookmark_id` - Campo `bookmark_id` presente
4. `test_logger_custom_fields` - Campos extras inclusos

**Validation:**
```bash
python -m pytest tests/test_logger.py -v
```

---

## Sprint 6: Webhook Server (Real-time Processing)

**Goal:** Endpoint HTTP para processamento via iOS Share Sheet.
**Demo (Automated):** `curl POST /process` → 202 Accepted → nota gerada → notificação enviada.
**Demo (Manual):** iOS Shortcut configured and tested (separate validation, not blocking).

### Task 6.1: Webhook Server - Basic HTTP
**Scope:** HTTPServer com GET /health e POST /process.
**File:** `src/webhook_server.py`
**Tests:** `tests/test_webhook_server.py`

**Test Cases:**
1. `test_health_endpoint` - GET /health → 200 + JSON
2. `test_process_requires_post` - GET /process → 404
3. `test_process_accepts_json` - POST com JSON body
4. `test_process_returns_202` - Resposta imediata 202

**Validation:**
```bash
python -m pytest tests/test_webhook_server.py -v
# Manual: curl http://localhost:8766/health
```

---

### Task 6.2: Webhook Server - Authentication
**Scope:** Bearer token authentication.
**File:** `src/webhook_server.py` (extend)
**Tests:** `tests/test_webhook_server.py` (extend)

**Test Cases:**
1. `test_auth_required_when_token_set` - 401 sem header
2. `test_auth_accepts_valid_token` - 202 com token correto
3. `test_auth_rejects_invalid_token` - 401 com token errado
4. `test_auth_optional_in_dev` - Sem token env → auth desabilitado

**Validation:**
```bash
python -m pytest tests/test_webhook_server.py::test_auth* -v
```

---

### Task 6.3: Webhook Server - Background Processing
**Scope:** Processar em thread separada após 202.
**File:** `src/webhook_server.py` (extend)
**Tests:** `tests/test_webhook_server.py` (extend)

**Test Cases:**
1. `test_process_spawns_thread` - Thread criada
2. `test_process_completes_async` - Nota gerada após response
3. `test_process_handles_error_gracefully` - Erro não crasha server

**Validation:**
```bash
python -m pytest tests/test_webhook_server.py -v
```

---

### Task 6.4: Webhook Server - URL Validation
**Scope:** Validar URL do Twitter/X antes de processar.
**File:** `src/webhook_server.py` (extend)
**Tests:** `tests/test_webhook_server.py` (extend)

**Test Cases:**
1. `test_validates_twitter_url` - twitter.com/x.com aceitos
2. `test_rejects_non_twitter_url` - Outras URLs → 400
3. `test_extracts_tweet_id` - ID extraído da URL

**Validation:**
```bash
python -m pytest tests/test_webhook_server.py::test_validates* -v
```

---

### Task 6.5: Notification Integration
**Scope:** Notificar via Telegram após processamento.
**File:** `src/core/notifier.py`
**Tests:** `tests/test_notifier.py`

**Test Cases:**
1. `test_notify_calls_command` - subprocess.run com mensagem
2. `test_notify_formats_success` - Formato enriquecido sucesso
3. `test_notify_formats_error` - Formato enriquecido erro
4. `test_notify_handles_missing_command` - Graceful se comando não existe

**Validation:**
```bash
python -m pytest tests/test_notifier.py -v
```

---

### Task 6.6: Webhook Integration with Pipeline
**Scope:** Webhook usa pipeline completo para processar.
**File:** `src/webhook_server.py` (extend)
**Tests:** `tests/test_webhook_server.py` (extend)

**Test Cases:**
1. `test_webhook_uses_pipeline` - Pipeline chamado
2. `test_webhook_notifies_on_complete` - Notificação enviada
3. `test_webhook_updates_state` - State manager atualizado

**Validation:**
```bash
python -m pytest tests/test_webhook_server.py -v
# Integration: POST URL real, verificar nota
```

---

## Sprint 7: Polling Daemon & Main Entry Point

**Goal:** Daemon que monitora backlog e processa automaticamente.
**Demo:** Drop export em data/backlog/ → processado em 2min → notas geradas.

### Task 7.0: Duplicate Detection
**Scope:** Detect and skip duplicate bookmarks across multiple exports.
**File:** `src/core/deduplicator.py`
**Tests:** `tests/test_deduplicator.py`

**Strategy:**
- Primary key: Tweet ID
- Check state manager before processing
- Log skipped duplicates

**Test Cases:**
1. `test_detects_duplicate_by_id` - Same tweet ID → skip
2. `test_allows_different_ids` - Different IDs → process both
3. `test_logs_skipped_duplicates` - Logging for visibility
4. `test_counts_duplicates_in_stats` - Stats include duplicate count

**Validation:**
```bash
python -m pytest tests/test_deduplicator.py -v
```

---

### Task 7.0a: Backlog File Management
**Scope:** Archive or delete processed export files.
**File:** `src/core/backlog_manager.py`
**Tests:** `tests/test_backlog_manager.py`

**Strategy:**
- After processing: move to `data/backlog/processed/`
- Keep last 30 days of processed files
- Clean older files automatically

**Test Cases:**
1. `test_moves_processed_file` - File moved to processed/
2. `test_preserves_recent_files` - Files < 30 days kept
3. `test_cleans_old_files` - Files > 30 days deleted
4. `test_handles_missing_file` - Graceful if file already moved

**Validation:**
```bash
python -m pytest tests/test_backlog_manager.py -v
```

---

### Task 7.1: Directory Watcher
**Scope:** Monitorar data/backlog/ para novos arquivos.
**File:** `src/core/watcher.py`
**Tests:** `tests/test_watcher.py`

**Test Cases:**
1. `test_watcher_detects_new_file` - Arquivo novo detectado
2. `test_watcher_ignores_processed` - Arquivo já processado ignorado
3. `test_watcher_handles_empty_dir` - Diretório vazio OK

**Validation:**
```bash
python -m pytest tests/test_watcher.py -v
```

---

### Task 7.2: Main Entry Point - Once Mode
**Scope:** `python main.py --once` processa backlog uma vez.
**File:** `src/main.py`
**Tests:** `tests/test_main.py`

**Test Cases:**
1. `test_main_once_processes_backlog` - Processa arquivos existentes
2. `test_main_once_exits_after` - Termina após processar
3. `test_main_once_reports_stats` - Mostra estatísticas

**Validation:**
```bash
python -m pytest tests/test_main.py::test_main_once* -v
# Manual: python src/main.py --once
```

---

### Task 7.3: Main Entry Point - Daemon Mode
**Scope:** `python main.py` roda como daemon com polling.
**File:** `src/main.py` (extend)
**Tests:** `tests/test_main.py` (extend)

**Test Cases:**
1. `test_main_daemon_runs_loop` - Loop de polling
2. `test_main_daemon_interval_2min` - Intervalo de 2 minutos
3. `test_main_daemon_graceful_shutdown` - SIGTERM para limpo

**Validation:**
```bash
python -m pytest tests/test_main.py::test_main_daemon* -v
# Manual: python src/main.py (Ctrl+C para parar)
```

---

### Task 7.4: CLI Arguments
**Scope:** argparse para --once, --port, --verbose, etc.
**File:** `src/main.py` (extend)
**Tests:** `tests/test_main.py` (extend)

**Test Cases:**
1. `test_cli_once_flag` - --once reconhecido
2. `test_cli_port_flag` - --port 8766
3. `test_cli_verbose_flag` - --verbose aumenta log level
4. `test_cli_help` - --help mostra uso

**Validation:**
```bash
python src/main.py --help
```

---

### Task 7.5: Daily Summary
**Scope:** Resumo diário enviado via notificação.
**File:** `src/core/summary.py`
**Tests:** `tests/test_summary.py`

**Test Cases:**
1. `test_summary_counts_by_type` - Contagem por tipo
2. `test_summary_includes_errors` - Erros listados
3. `test_summary_average_duration` - Duração média

**Validation:**
```bash
python -m pytest tests/test_summary.py -v
```

---

## Sprint 8: Deployment & Production Hardening

**Goal:** Deploy no Mac Mini com launchd e iOS Shortcut.
**Demo:** Sistema rodando 24/7, iOS Shortcut funcionando, backlog sendo processado.

### Task 8.1: Setup Script - Venv Creation
**Scope:** Script para criar venv persistente.
**File:** `setup-macos.sh`
**Validation:**
```bash
./setup-macos.sh
source /workspace/.mcp-tools/twitter-processor/venv/bin/activate
python -c "import anthropic; print('OK')"
```

---

### Task 8.2: Setup Script - Launchd Plist (Daemon)
**Scope:** Plist para daemon de polling.
**Files:**
- `setup-macos.sh` (extend)
- `deploy/com.mp3fbf.twitter-processor.plist`

**Validation:**
```bash
# No Mac Mini:
launchctl load ~/Library/LaunchAgents/com.mp3fbf.twitter-processor.plist
launchctl list | grep twitter
```

---

### Task 8.3: Setup Script - Launchd Plist (Webhook)
**Scope:** Plist para webhook server.
**Files:**
- `setup-macos.sh` (extend)
- `deploy/com.mp3fbf.twitter-webhook.plist`

**Validation:**
```bash
# No Mac Mini:
launchctl load ~/Library/LaunchAgents/com.mp3fbf.twitter-webhook.plist
curl http://localhost:8766/health
```

---

### Task 8.4: iOS Shortcut Documentation
**Scope:** Documentar criação do Shortcut.
**File:** `docs/ios-shortcut.md`

**Content:**
1. Criar novo Shortcut
2. Ação: Get URL from Share Sheet
3. Ação: Get Contents of URL (POST)
4. Header: Authorization: Bearer {token}
5. Body: {"url": "[URL]"}

**Validation:** Manual - criar shortcut e testar

---

### Task 8.5: Environment Variables Documentation
**Scope:** Documentar todas as env vars necessárias.
**File:** `docs/configuration.md`

**Variables:**
- `ANTHROPIC_API_KEY`
- `TWITTER_WEBHOOK_TOKEN`
- `TWITTER_OUTPUT_DIR` (default: /workspace/notes/twitter/)

**Validation:** README atualizado com referência

---

### Task 8.6: Integration Test Suite
**Scope:** Testes de integração end-to-end.
**File:** `tests/test_integration.py`
**Fixture:** `tests/conftest.py` - temp directory fixture

**Test Cases:**
1. `test_full_pipeline_all_types` - Processa VIDEO, THREAD, LINK, TWEET
2. `test_webhook_e2e` - POST → nota gerada
3. `test_backlog_processing` - Drop arquivo → processado
4. `test_integration_uses_temp_dirs` - Tests don't touch real /workspace/notes/

**Important:** All integration tests use `@pytest.fixture` for temp output directory to avoid polluting real filesystem.

**Validation:**
```bash
python -m pytest tests/test_integration.py -v --integration
```

---

### Task 8.7: Health Check & Monitoring
**Scope:** Endpoint /metrics e health check.
**File:** `src/webhook_server.py` (extend)

**Endpoints:**
- GET /health - Status básico
- GET /metrics - Contadores (processados, erros, uptime)

**Validation:**
```bash
curl http://localhost:8766/metrics
```

---

### Task 8.8: Final Documentation
**Scope:** README completo e troubleshooting.
**File:** `README.md` (update)

**Sections:**
- Quick Start (atualizado)
- Configuration
- Deployment
- Troubleshooting
- Contributing

**Validation:** Review manual do README

---

## Summary

| Sprint | Tasks | Goal | Demo |
|--------|-------|------|------|
| 0 | 2 | Discovery | Fixtures + skill integration docs |
| 1 | 7 | Foundation | pytest passa, bookmarks parseados |
| 2 | 9 | Classification & Tweets | Tweet → nota Obsidian |
| 3 | 7 | Video & Thread | YouTube/Thread → nota completa |
| 4 | 8 | Link & LLM | Artigo → nota com LLM extraction |
| 5 | 5 | Rate Limiting | 20 bookmarks com rate limit |
| 6 | 6 | Webhook | curl POST → nota + notificação |
| 7 | 7 | Polling Daemon | Drop export → processado |
| 8 | 8 | Deployment | Sistema 24/7 no Mac Mini |
| **Total** | **59** | | |

---

## Reviewer Notes (from Plan Agent)

### Critical Pre-requisites
1. **Sprint 0 must complete before any coding** - Twillot format and skill integration are high-risk unknowns
2. **bird CLI availability** - May need to run processors on Mac host, not in container

### High-Risk Areas to Monitor
- Task 3.1-3.4: Skill subprocess invocation may need redesign based on Sprint 0 findings
- Task 4.4: LLM output parsing - add explicit retry and fallback logic
- Task 2.2: Thread detection heuristics - tune based on real data from Sprint 0

### Design Decisions Deferred to Sprint 0
- Container vs host execution for skill calls
- Exact Twillot JSON schema and field mapping
- Whether to import skills as libraries or call via subprocess

---

## Output

**After approval, this plan will be saved to:**
`/workspace/twitter-bookmark-processor/docs/SPRINTS.md`

And committed to the repository.
