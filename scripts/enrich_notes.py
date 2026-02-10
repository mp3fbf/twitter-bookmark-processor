#!/usr/bin/env python3
"""Enrich twitter bookmark notes with Obsidian graph metadata.

Reads each note, analyzes content, adds:
- Hierarchical tags (topic/x, person/x, source/twitter)
- up: field pointing to Atlas MOC
- ## Topics section with [[wikilinks]]

Usage:
    python3 scripts/enrich_notes.py /path/to/notes/twitter/
    python3 scripts/enrich_notes.py /path/to/notes/twitter/ --dry-run
    python3 scripts/enrich_notes.py /path/to/notes/twitter/ --limit 5
"""

import os
import re
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────
# TOPIC DEFINITIONS
# Each topic has: keywords (matched against title+body),
# wikilink name, hierarchical tag, and optional MOC
# ─────────────────────────────────────────────────────────

TOPICS = [
    # ── AI Coding ──
    {
        "id": "claude-code",
        "keywords": [r"\bclaude code\b", r"\bclaude-code\b", r"\bclaudecode\b"],
        "tag": "topic/claude-code",
        "wikilink": "Claude Code",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "claude",
        "keywords": [r"\bclaude\b(?! code)", r"\banthropic\b"],
        "tag": "topic/claude",
        "wikilink": "Claude",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "coding-agents",
        "keywords": [
            r"\bcoding agent", r"\bai agent", r"\bagent\b.*\bcod",
            r"\bralph\b", r"\bralph.wiggum\b", r"\bafk.*loop\b",
            r"\bswarm agent", r"\borchestrat",
        ],
        "tag": "topic/coding-agents",
        "wikilink": "Coding Agents",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "ai-coding",
        "keywords": [
            r"\bai coding\b", r"\bvibe cod", r"\bai.assist",
            r"\bcursor\b", r"\bcopilot\b", r"\bcodex\b",
            r"\byolo mode\b", r"\bgithub copilot\b",
        ],
        "tag": "topic/ai-coding",
        "wikilink": "AI Coding",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "skills",
        "keywords": [
            r"\bskill[s]?\b.*\b(agent|claude|code|ai)\b",
            r"\b(agent|claude|code|ai)\b.*\bskill[s]?\b",
            r"\bSKILL\.md\b", r"\bopenski[l]+s\b",
            r"\bclawhu[b]\b", r"\bopenclaw\b",
            r"\bSKILLs\b", r"\bskills\b.*\bstandard\b",
            r"\bskills\b.*\becosystem\b",
        ],
        "tag": "topic/ai-skills",
        "wikilink": "AI Skills",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "plugins",
        "keywords": [
            r"\bplugin[s]?\b", r"\bcompound.engineering\b",
            r"\bmcp\b.*\b(server|app)\b", r"\b(server|app)\b.*\bmcp\b",
            r"\bmcp apps\b",
            r"\bcowork\b",
        ],
        "tag": "topic/plugins",
        "wikilink": "Plugins",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "prompt-engineering",
        "keywords": [
            r"\bprompt\b.*\bengineering\b", r"\bmeta.prompt\b",
            r"\bsystem prompt\b", r"\bcontext engineer\b",
        ],
        "tag": "topic/prompt-engineering",
        "wikilink": "Prompt Engineering",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "agent-sdk",
        "keywords": [
            r"\bagent sdk\b", r"\bclaude agent sdk\b",
        ],
        "tag": "topic/agent-sdk",
        "wikilink": "Claude Agent SDK",
        "moc": "+Atlas/AI-Coding",
    },
    # ── AI/ML General ──
    {
        "id": "llm",
        "keywords": [
            r"\bllm[s]?\b", r"\blarge language model\b",
            r"\bgpt\b", r"\blanguage model\b",
        ],
        "tag": "topic/llm",
        "wikilink": "LLMs",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "ai-general",
        "keywords": [
            r"\bartificial intelligence\b", r"\bmachine learning\b",
            r"\bdeep learning\b", r"\bneural net\b",
        ],
        "tag": "topic/ai",
        "wikilink": "Artificial Intelligence",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "voice-ai",
        "keywords": [
            r"\btts\b", r"\btext.to.speech\b", r"\bvoice\b.*\b(clone|ai|model)\b",
            r"\bchatterbox\b", r"\bmirage\b.*\bvoice\b", r"\bsotto\b",
            r"\binworld\b.*\btts\b",
        ],
        "tag": "topic/voice-ai",
        "wikilink": "Voice AI",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "computer-vision",
        "keywords": [
            r"\bcomputer vision\b", r"\bimage generat\b",
            r"\bhand tracking\b", r"\bvision.?os\b", r"\bmediapipe\b",
            r"\bdeepfake\b", r"\bmotion capture\b",
        ],
        "tag": "topic/computer-vision",
        "wikilink": "Computer Vision",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "gemini",
        "keywords": [r"\bgemini\b", r"\bmedgemma\b"],
        "tag": "topic/gemini",
        "wikilink": "Gemini",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "openai",
        "keywords": [r"\bopenai\b", r"\bchatgpt\b"],
        "tag": "topic/openai",
        "wikilink": "OpenAI",
        "moc": "+Atlas/AI-Coding",
    },
    # ── Software Engineering ──
    {
        "id": "typescript",
        "keywords": [r"\btypescript\b", r"\b\.ts\b"],
        "tag": "topic/typescript",
        "wikilink": "TypeScript",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "react",
        "keywords": [r"\breact\b", r"\breact.?native\b", r"\bnext\.?js\b", r"\bexpo\b"],
        "tag": "topic/react",
        "wikilink": "React",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "python",
        "keywords": [r"\bpython\b", r"\bfastapi\b", r"\bdjango\b"],
        "tag": "topic/python",
        "wikilink": "Python",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "rust",
        "keywords": [r"\brust\b(?!.*stain)"],
        "tag": "topic/rust",
        "wikilink": "Rust",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "swift",
        "keywords": [r"\bswift\b", r"\bswiftui\b", r"\bxcode\b", r"\bios\b.*\bdev"],
        "tag": "topic/swift",
        "wikilink": "Swift",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "web-dev",
        "keywords": [
            r"\bweb dev\b", r"\bfrontend\b", r"\bfront.end\b",
            r"\bcss\b", r"\btailwind\b", r"\bshadcn\b",
            r"\blanding page\b", r"\bui.?ux\b",
            r"\bshader[s]?\b", r"\bwebgl\b",
        ],
        "tag": "topic/web-development",
        "wikilink": "Web Development",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "api-design",
        "keywords": [
            r"\bwebhook[s]?\b", r"\bwebsocket[s]?\b",
            r"\bapi\b.*\b(design|rest|graphql)\b",
            r"\bstripe\b.*\b(api|payment)\b",
        ],
        "tag": "topic/api-design",
        "wikilink": "API Design",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "devtools",
        "keywords": [
            r"\bdevtool[s]?\b", r"\bcli\b", r"\bterminal\b",
            r"\bgithub\b", r"\bgit\b(?!ea)",
            r"\bvercel\b", r"\bdocker\b",
        ],
        "tag": "topic/devtools",
        "wikilink": "Developer Tools",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "browser-automation",
        "keywords": [
            r"\bbrowser.?auto\b", r"\bagent.?browser\b",
            r"\bheadless\b", r"\bplaywright\b", r"\bpuppeteer\b",
            r"\bchrome extension\b", r"\bweb.?scrap\b",
            r"\bhyperbrowser\b", r"\bstealth mode\b",
        ],
        "tag": "topic/browser-automation",
        "wikilink": "Browser Automation",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "software-architecture",
        "keywords": [
            r"\barchitect\b", r"\bdesign pattern\b",
            r"\bsoftware develop\b", r"\brefactor\b",
            r"\bmicroservice\b", r"\bmonolith\b",
        ],
        "tag": "topic/software-architecture",
        "wikilink": "Software Architecture",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "open-source",
        "keywords": [r"\bopen.source\b", r"\bopen source\b", r"\bfoss\b"],
        "tag": "topic/open-source",
        "wikilink": "Open Source",
        "moc": "+Atlas/Software-Engineering",
    },
    # ── Productivity & PKM ──
    {
        "id": "obsidian",
        "keywords": [r"\bobsidian\b", r"\bpkm\b", r"\bpersonal knowledge\b"],
        "tag": "topic/obsidian",
        "wikilink": "Obsidian",
    },
    {
        "id": "automation",
        "keywords": [
            r"\bn8n\b", r"\bautomation\b", r"\bworkflow\b",
            r"\bnewsletter.*auto\b", r"\bauto.*newsletter\b",
            r"\bcron job\b",
        ],
        "tag": "topic/automation",
        "wikilink": "Automation",
    },
    {
        "id": "tailscale",
        "keywords": [r"\btailscale\b"],
        "tag": "topic/tailscale",
        "wikilink": "Tailscale",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "rss",
        "keywords": [r"\brss\b", r"\batom feed\b"],
        "tag": "topic/rss",
        "wikilink": "RSS",
    },
    # ── Football ──
    {
        "id": "flamengo",
        "keywords": [r"\bflamengo\b", r"\bmengão\b", r"\brubronegro\b"],
        "tag": "topic/flamengo",
        "wikilink": "Flamengo",
    },
    {
        "id": "football",
        "keywords": [
            r"\bfutebol\b", r"\bfootball\b", r"\bsoccer\b",
            r"\bbrasileirão\b", r"\blibertadores\b",
            r"\bcopa união\b", r"\bpenalt\b",
            r"\bcorinthians\b", r"\bpalmeiras\b",
            r"\bgabigol\b", r"\bzico\b", r"\bpedro\b.*\bgol\b",
        ],
        "tag": "topic/football",
        "wikilink": "Football",
    },
    {
        "id": "var",
        "keywords": [r"\bvar\b", r"\brefere\b", r"\bárbitro\b", r"\bwilton\b"],
        "tag": "topic/var",
        "wikilink": "VAR",
    },
    # ── Business ──
    {
        "id": "startup",
        "keywords": [
            r"\bstartup\b", r"\bfounder\b", r"\bsaas\b",
            r"\bproduct market fit\b", r"\bmrr\b",
            r"\brevenue\b", r"\bgrowth\b",
        ],
        "tag": "topic/startup",
        "wikilink": "Startups",
    },
    {
        "id": "marketing",
        "keywords": [r"\bmarketing\b", r"\bconversion\b", r"\bseo\b"],
        "tag": "topic/marketing",
        "wikilink": "Marketing",
    },
    # ── Education ──
    {
        "id": "education",
        "keywords": [
            r"\beducation\b", r"\blearn\b.*\b(fract|math|cod)",
            r"\btutorial\b", r"\bbeginner\b.*\bguide\b",
            r"\bteaching\b", r"\bclass\b.*\bmba\b",
        ],
        "tag": "topic/education",
        "wikilink": "Education",
    },
    # ── Medicine/Health ──
    {
        "id": "medicine",
        "keywords": [
            r"\bmedicine\b", r"\bclinical\b", r"\bmedical\b",
            r"\bhealth\b.*\b(ai|tech)\b", r"\bmedgemma\b",
            r"\bdiagnos\b",
        ],
        "tag": "topic/medicine",
        "wikilink": "Medicine & AI",
    },
    # ── Specific Tools ──
    {
        "id": "apify",
        "keywords": [r"\bapify\b"],
        "tag": "topic/apify",
        "wikilink": "Apify",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "vercel",
        "keywords": [r"\bvercel\b", r"\bv0\b.*\bdev\b"],
        "tag": "topic/vercel",
        "wikilink": "Vercel",
        "moc": "+Atlas/Software-Engineering",
    },
    {
        "id": "telegram",
        "keywords": [r"\btelegram\b", r"\bwhatsapp\b", r"\bchatbot\b", r"\bclawdbot\b"],
        "tag": "topic/messaging-bots",
        "wikilink": "Messaging Bots",
    },
    {
        "id": "apple",
        "keywords": [
            r"\bapple\b(?! ?script)", r"\biphone\b", r"\bipad\b",
            r"\bmacos\b.*\b(app|menu)\b", r"\bapple tv\b",
            r"\bapple music\b",
        ],
        "tag": "topic/apple",
        "wikilink": "Apple",
    },
    # ── Humor/Memes ──
    {
        "id": "meme",
        "keywords": [
            r"\bmeme\b", r"\bhumor\b", r"\bsátira\b",
            r"\bdesafortunados\b", r"\bfunny\b.*\bfootball\b",
        ],
        "tag": "topic/meme",
        "wikilink": "Memes",
    },
    # ── Additional catch-alls ──
    {
        "id": "x-platform",
        "keywords": [
            r"\bx api\b", r"\btwitter api\b", r"\bdeveloper\.x\.com\b",
            r"\balgoritmo do x\b", r"\bx algorithm\b",
        ],
        "tag": "topic/x-platform",
        "wikilink": "X Platform",
    },
    {
        "id": "saas",
        "keywords": [
            r"\bsaas\b", r"\bcharging\b.*\b\$/?(year|month|yr|mo)\b",
            r"\b\d+k?\s*/\s*(year|month)\b",
            r"\bfirst 1000 (user|customer)\b",
        ],
        "tag": "topic/saas",
        "wikilink": "SaaS",
    },
    {
        "id": "generative-ai",
        "keywords": [
            r"\bgenerat\w+ ai\b", r"\bai generat\b",
            r"\bimage generat\b", r"\bsprite\b", r"\bnano.banana\b",
            r"\b3d.*\bgenerat\b",
        ],
        "tag": "topic/generative-ai",
        "wikilink": "Generative AI",
        "moc": "+Atlas/AI-Coding",
    },
    {
        "id": "hiring",
        "keywords": [
            r"\bhiring\b", r"\binterview\b.*\btech\b",
            r"\btechnical evaluat\b",
        ],
        "tag": "topic/hiring",
        "wikilink": "Technical Hiring",
    },
    {
        "id": "productivity-personal",
        "keywords": [
            r"\bmorning\b.*\bbrief\b", r"\bbrief\b.*\bmorning\b",
            r"\bpersonal (os|system|software)\b",
            r"\bspeed read\b",
        ],
        "tag": "topic/personal-productivity",
        "wikilink": "Personal Productivity",
    },
    {
        "id": "strava",
        "keywords": [r"\bstrava\b"],
        "tag": "topic/strava",
        "wikilink": "Strava",
    },
]

# Known people: author_username -> display name
KNOWN_PEOPLE = {
    "borischerny": "Boris Cherny",
    "mattpocockuk": "Matt Pocock",
    "tobi": "Tobi Lütke",
    "karpathy": "Andrej Karpathy",
    "swyx": "Swyx",
    "thekitze": "Kitze",
    "zeeg": "David Cramer",
    "raaborncreates": "Jesse Raaborn",
    "kevinrose": "Kevin Rose",
    "amasad": "Amjad Masad",
    "simonw": "Simon Willison",
    "levelsio": "Pieter Levels",
    "guillermo": "Guillermo Rauch",
    "alexalbert__": "Alex Albert",
    "geoffreyhuntley": "Geoffrey Huntley",
    "realgalego": "Augusto Galego",
    "quiverquant": "Quiver Quantitative",
    "petersteinberger": "Peter Steinberger",
    "terresatorres": "Teresa Torres",
    "amorriscode": "Anthony Morris",
}

# Content type → tag
CONTENT_TYPE_TAGS = {
    "tweet": "twitter/tweet",
    "thread": "twitter/thread",
    "video": "twitter/video",
    "link": "twitter/link",
}


def parse_note(filepath: str) -> dict:
    """Parse a markdown note into frontmatter dict + body string."""
    with open(filepath) as f:
        content = f.read()

    if not content.startswith("---"):
        return {"frontmatter": {}, "body": content, "raw": content}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {"frontmatter": {}, "body": content, "raw": content}

    fm_text = parts[1]
    body = parts[2]

    # Parse YAML manually (safe, no dependency)
    fm = {}
    current_list_key = None
    current_list = []

    for line in fm_text.strip().split("\n"):
        if line.startswith("  - "):
            if current_list_key:
                current_list.append(line.strip()[2:].strip())
            continue
        else:
            # Save previous list if any
            if current_list_key:
                fm[current_list_key] = current_list
                current_list_key = None
                current_list = []

            if ": " in line:
                key, val = line.split(": ", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if not val:
                    # Could be start of a list
                    pass
                else:
                    fm[key] = val
            elif line.strip().endswith(":"):
                current_list_key = line.strip()[:-1]
                current_list = []

    if current_list_key:
        fm[current_list_key] = current_list

    return {"frontmatter": fm, "body": body, "raw": content}


def analyze_topics(title: str, body: str) -> list[dict]:
    """Match content against topic definitions. Returns matched topics."""
    text = f"{title}\n{body}".lower()
    matched = []
    seen_ids = set()

    for topic in TOPICS:
        if topic["id"] in seen_ids:
            continue
        for kw in topic["keywords"]:
            if re.search(kw, text, re.IGNORECASE):
                matched.append(topic)
                seen_ids.add(topic["id"])
                break

    return matched


def build_tags(matched_topics: list[dict], content_type: str, author: str) -> list[str]:
    """Build hierarchical tag list."""
    tags = ["source/twitter"]

    # Content type tag
    ct_tag = CONTENT_TYPE_TAGS.get(content_type, "twitter/tweet")
    tags.append(ct_tag)

    # Person tag for author
    clean_author = author.lower().lstrip("@").strip()
    if clean_author:
        tags.append(f"person/{clean_author}")

    # Topic tags
    for topic in matched_topics:
        if topic["tag"] not in tags:
            tags.append(topic["tag"])

    return tags


def build_wikilinks(matched_topics: list[dict], author: str) -> list[str]:
    """Build wikilink list from matched topics."""
    links = []

    # Person wikilink for known people
    clean_author = author.lower().lstrip("@").strip()
    if clean_author in KNOWN_PEOPLE:
        links.append(KNOWN_PEOPLE[clean_author])

    # Topic wikilinks
    for topic in matched_topics:
        wl = topic["wikilink"]
        if wl not in links:
            links.append(wl)

    return links


def resolve_moc(matched_topics: list[dict]) -> str | None:
    """Find the best Atlas MOC for up: field."""
    # Priority: most specific match first
    for topic in matched_topics:
        moc = topic.get("moc")
        if moc:
            return moc
    return None


def rebuild_note(parsed: dict, tags: list[str], wikilinks: list[str], moc: str | None) -> str:
    """Rebuild the note with enriched frontmatter and Topics section."""
    fm = parsed["frontmatter"]
    body = parsed["body"]

    # Build new frontmatter
    lines = ["---"]
    # Preserve original fields in order
    field_order = ["title", "author", "source", "type"]
    for key in field_order:
        if key in fm:
            val = fm[key]
            if any(c in str(val) for c in [':', '#', '"', "'", '[', ']', '{', '}']):
                lines.append(f'{key}: "{val}"')
            else:
                lines.append(f"{key}: {val}")

    # Add up: field
    if moc:
        lines.append(f'up: "[[{moc}]]"')

    # Add hierarchical tags
    lines.append("tags:")
    for tag in tags:
        lines.append(f"  - {tag}")

    # Preserve remaining original fields
    for key in fm:
        if key in field_order or key == "tags":
            continue
        val = fm[key]
        if isinstance(val, list):
            continue  # Skip old tags list, already replaced
        if any(c in str(val) for c in [':', '#', '"', "'", '[', ']', '{', '}']):
            lines.append(f'{key}: "{val}"')
        else:
            lines.append(f"{key}: {val}")

    lines.append("---")

    # Body: strip existing ## Topics section if any (shouldn't exist yet)
    body_clean = re.sub(
        r'\n## Topics\n.*?(?=\n## |\n---\n|\Z)',
        '',
        body,
        flags=re.DOTALL,
    )

    # Remove trailing footer to re-add it
    footer_pattern = r'\n---\n\*Processed by twitter-bookmark-processor.*?\*\s*$'
    body_no_footer = re.sub(footer_pattern, '', body_clean, flags=re.DOTALL)

    # Build Topics section
    topics_section = ""
    if wikilinks:
        topics_md = " · ".join(f"[[{wl}]]" for wl in wikilinks)
        topics_section = f"\n## Topics\n\n{topics_md}\n"

    # Reconstruct
    result = "\n".join(lines) + body_no_footer.rstrip()
    if topics_section:
        result += "\n" + topics_section
    result += "\n\n---\n*Processed by twitter-bookmark-processor v0.2.0*\n"

    return result


def process_notes(notes_dir: str, dry_run: bool = False, limit: int = 0) -> dict:
    """Process all notes in directory."""
    stats = {
        "total": 0, "enriched": 0, "skipped_empty": 0,
        "topics_found": {}, "moc_count": {},
    }

    files = sorted(f for f in os.listdir(notes_dir) if f.endswith(".md"))
    if limit:
        files = files[:limit]

    for fname in files:
        filepath = os.path.join(notes_dir, fname)
        parsed = parse_note(filepath)
        fm = parsed["frontmatter"]
        stats["total"] += 1

        title = fm.get("title", fname.replace(".md", ""))
        author = fm.get("author", "")
        content_type = fm.get("type", "tweet")
        body = parsed["body"]

        # Analyze
        matched = analyze_topics(title, body)

        if not matched:
            # Even empty notes get base tags
            stats["skipped_empty"] += 1

        tags = build_tags(matched, content_type, author)
        wikilinks = build_wikilinks(matched, author)
        moc = resolve_moc(matched)

        # Track stats
        for topic in matched:
            tid = topic["id"]
            stats["topics_found"][tid] = stats["topics_found"].get(tid, 0) + 1
        if moc:
            stats["moc_count"][moc] = stats["moc_count"].get(moc, 0) + 1

        # Rebuild
        new_content = rebuild_note(parsed, tags, wikilinks, moc)
        stats["enriched"] += 1

        if dry_run:
            if matched:
                topics_str = ", ".join(t["id"] for t in matched[:5])
                print(f"  {fname[:60]:<62s} → {topics_str}")
        else:
            with open(filepath, "w") as f:
                f.write(new_content)

    return stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/enrich_notes.py <notes_dir> [--dry-run] [--limit N]")
        sys.exit(1)

    notes_dir = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    limit = 0
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    if not os.path.isdir(notes_dir):
        print(f"ERROR: Not a directory: {notes_dir}")
        sys.exit(1)

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n[enrich_notes] Processing notes in {notes_dir} ({mode})")
    if limit:
        print(f"[enrich_notes] Limit: {limit} notes")

    stats = process_notes(notes_dir, dry_run=dry_run, limit=limit)

    print(f"\n{'='*50}")
    print(f"Results:")
    print(f"  Total:    {stats['total']}")
    print(f"  Enriched: {stats['enriched']}")
    print(f"  No topic match: {stats['skipped_empty']}")

    if stats["topics_found"]:
        print(f"\nTop topics:")
        for tid, count in sorted(stats["topics_found"].items(), key=lambda x: -x[1])[:15]:
            print(f"  {count:3d}x  {tid}")

    if stats["moc_count"]:
        print(f"\nMOC assignments:")
        for moc, count in sorted(stats["moc_count"].items(), key=lambda x: -x[1]):
            print(f"  {count:3d}x  {moc}")


if __name__ == "__main__":
    main()
