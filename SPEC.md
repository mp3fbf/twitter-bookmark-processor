# Twitter Bookmark Processor

## Objetivo
Sistema automatico para processar bookmarks do Twitter/X, extrair conhecimento e gerar notas Obsidian.

## Requisitos
- **Deteccao hibrida:** Polling (2 min) + iOS Share Sheet (imediato)
- **Tipos de conteudo:** Videos -> /yt skill | Threads -> /twitter skill | Links -> LLM extract | Tweets simples
- **Output:** `/workspace/notes/twitter/` (Obsidian-compatible)
- **Backlog:** ~500-2000 bookmarks existentes, processar em paralelo

---

## Arquitetura

```
/workspace/twitter-bookmark-processor/
├── src/
│   ├── main.py                  # Polling daemon
│   ├── webhook_server.py        # iOS Share Sheet endpoint (porta 8766)
│   ├── core/
│   │   ├── bookmark.py          # Data model
│   │   ├── classifier.py        # VIDEO/THREAD/LINK/TWEET
│   │   └── state_manager.py     # Tracking de processados
│   ├── sources/
│   │   └── twillot_reader.py    # Parse Twillot JSON export
│   ├── processors/
│   │   ├── video_processor.py   # Delega para /youtube-video skill
│   │   ├── thread_processor.py  # Delega para /twitter skill
│   │   ├── link_processor.py    # LLM knowledge extraction
│   │   └── tweet_processor.py   # Tweet simples + categorizacao
│   └── output/
│       └── obsidian_writer.py   # Gera notas .md com frontmatter
├── data/
│   ├── state.json               # IDs processados + stats
│   └── backlog/                 # Twillot exports
└── setup-macos.sh               # Configura launchd services
```

**Venv persistente:** `/workspace/.mcp-tools/twitter-processor/venv/`

---

## Data Flow

### Polling (Background)
```
Twillot export -> data/backlog/ -> twillot_reader.py -> classifier
     |
[VIDEO] -> youtube_processor.py (skill existente)
[THREAD] -> twitter_reader.py (skill existente)
[LINK] -> LLM extract (Claude Haiku)
[TWEET] -> categorize + save
     |
obsidian_writer -> /workspace/notes/twitter/{type}/
     |
notify "Processado: {titulo}"
```

### Share Sheet (Imediato)
```
iOS Share -> Shortcut -> POST http://100.66.201.114:8766/process
     |
webhook_server.py -> 202 Accepted -> processa em thread
     |
Mesmo flow acima
```

---

## Integracao com Skills Existentes

| Tipo | Script | Chamada |
|------|--------|---------|
| Video | `/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py` | `python3 {script} {url} --note -o /workspace/notes/twitter/videos/` |
| Thread | `/home/claude/.claude/skills/twitter/scripts/twitter_reader.py` | `python3 {script} {url} --thread --json` |

---

## Output Format (Obsidian)

```markdown
---
title: "Thread Title"
source: https://x.com/user/status/123
author: "@username"
type: thread|video|article|tweet
date_bookmarked: 2025-01-20
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
```

---

## Implementacao (Sequencia)

### Fase 1: Core
1. Criar estrutura do projeto
2. Setup venv em `/workspace/.mcp-tools/twitter-processor/`
3. `bookmark.py` - Data model com ContentType enum
4. `state_manager.py` - JSON-based state (padrao do kb_processor.py)
5. `twillot_reader.py` - Parse JSON export do Twillot

### Fase 2: Processors
6. `classifier.py` - Detecta VIDEO/THREAD/LINK/TWEET
7. `video_processor.py` - Integra com skill existente
8. `thread_processor.py` - Integra com skill existente
9. `tweet_processor.py` - Extracao basica

### Fase 3: Output & Webhook
10. `obsidian_writer.py` - Templates Jinja2 por tipo
11. `webhook_server.py` - Baseado no yt-webhook (porta 8766)
12. `main.py` - Polling daemon

### Fase 4: Deploy
13. `setup-macos.sh` - launchd plists
14. iOS Shortcut "Process Tweet"
15. Testar com backlog real

---

## Dependencias

```
anthropic>=0.18.0
httpx>=0.27.0
requests>=2.31.0
beautifulsoup4>=4.12.0
jinja2>=3.1.0
```

---

## Verificacao

1. **Unit test:** `python3 -m pytest tests/`
2. **Manual - Polling:**
   - Drop um export Twillot em `data/backlog/`
   - `python3 src/main.py --once`
   - Verificar nota gerada em `/workspace/notes/twitter/`
3. **Manual - Webhook:**
   - `python3 src/webhook_server.py`
   - `curl -X POST http://localhost:8766/process -d '{"url":"https://x.com/naval/status/123"}'`
   - Verificar notificacao Telegram
4. **Backlog:** Processar ~10 bookmarks variados (video, thread, link, tweet)

---

## Arquivos Criticos de Referencia

- `/workspace/_scripts/yt-webhook/server.py` - Padrao do webhook
- `/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py` - Skill de video
- `/home/claude/.claude/skills/twitter/scripts/twitter_reader.py` - Skill de twitter
- `/workspace/_scripts/kb_processor.py` - Padrao de state management
