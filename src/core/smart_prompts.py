"""Smart Prompts — content-type detection and tailored prompt generation.

Analyzes bookmarks to detect one of 12 smart content types (a finer-grained
layer on top of the routing ContentType), then builds a tailored LLM prompt
to extract maximum value from each bookmark.

Ported from twitter-bookmarks-app/smart_prompts.py.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SmartContentType(Enum):
    """Fine-grained content type for prompt selection.

    This is NOT the same as bookmark.ContentType (VIDEO/THREAD/LINK/TWEET),
    which is used for routing to processors. SmartContentType is a second
    layer used WITHIN processors to tailor the LLM prompt.
    """

    ARTICLE_LINK = "article_link"
    TOP_LIST = "top_list"
    TUTORIAL_GUIDE = "tutorial_guide"
    TOOL_ANNOUNCEMENT = "tool_announcement"
    CODE_SNIPPET = "code_snippet"
    OPINION_TAKE = "opinion_take"
    NEWS_UPDATE = "news_update"
    THREAD_CONTENT = "thread_content"
    VIDEO_CONTENT = "video_content"
    SCREENSHOT_INFO = "screenshot_info"
    MEME_HUMOR = "meme_humor"
    UNKNOWN = "unknown"


@dataclass
class SmartPrompt:
    """A prompt tailored to a specific smart content type."""

    content_type: SmartContentType
    prompt: str
    system_prompt: str
    expected_output: str


# Regex patterns for zero-cost content type detection
CONTENT_TYPE_PATTERNS: dict[SmartContentType, list[str]] = {
    SmartContentType.TOP_LIST: [
        r"top\s+\d+",
        r"best\s+\d+",
        r"\d+\s+best",
        r"\d+\s+things",
        r"my\s+favorite",
        r"ranking",
        r"list\s+of",
        r"\d+\s+ways",
        r"\d+\s+tips",
        r"\d+\s+tools",
    ],
    SmartContentType.TUTORIAL_GUIDE: [
        r"how\s+to",
        r"guide\s+to",
        r"tutorial",
        r"step\s+by\s+step",
        r"best\s+practices",
        r"tips\s+for",
        r"learn\s+how",
        r"checklist",
        r"prompt\s+guide",
        r"cheatsheet",
        r"cheat\s+sheet",
    ],
    SmartContentType.TOOL_ANNOUNCEMENT: [
        r"v\d+\.\d+",
        r"is\s+out",
        r"just\s+launched",
        r"releasing",
        r"announcing",
        r"introducing",
        r"new\s+release",
        r"open\s+source",
        r"just\s+dropped",
        r"npm\s+i",
        r"pip\s+install",
    ],
    SmartContentType.CODE_SNIPPET: [
        r"```",
        r"code:",
        r"prompt:",
        r"here's\s+how",
        r"example:",
        r"function\s+\w+",
        r"def\s+\w+",
        r"const\s+\w+",
        r"class\s+\w+",
    ],
    SmartContentType.OPINION_TAKE: [
        r"hot\s+take",
        r"unpopular\s+opinion",
        r"i\s+think",
        r"imo",
        r"my\s+take",
        r"controversial",
        r"change\s+my\s+mind",
    ],
    SmartContentType.NEWS_UPDATE: [
        r"breaking",
        r"just\s+in",
        r"announced",
        r"officially",
        r"confirmed",
        r"report:",
        r"update:",
    ],
    SmartContentType.THREAD_CONTENT: [
        r"thread",
        r"\d+/",
        r"a\s+thread",
        r"\(thread\)",
        r"1/",
    ],
}


class SmartPromptSelector:
    """Selects the best prompt for a given bookmark's content."""

    PROMPTS: dict[SmartContentType, SmartPrompt] = {
        SmartContentType.ARTICLE_LINK: SmartPrompt(
            content_type=SmartContentType.ARTICLE_LINK,
            prompt=(
                "This tweet links to an article or blog post.\n\n"
                "Tweet: {tweet_text}\n{link_content}\n\n"
                "TASK: Extract the KEY INFORMATION from this article that the user wanted to save.\n"
                "Provide:\n"
                "1. **Main Thesis**: What is the article arguing or explaining? (1-2 sentences)\n"
                "2. **Key Points**: The 3-5 most important takeaways (bullet points)\n"
                "3. **Practical Value**: What can the reader DO with this information?\n"
                "4. **Notable Quotes**: Any particularly insightful quotes worth saving\n\n"
                "Be specific and extract REAL INFORMATION, not vague descriptions."
            ),
            system_prompt="You are an expert at extracting key information from articles. Focus on actionable insights and specific details.",
            expected_output="Main thesis, key points, practical applications, notable quotes",
        ),
        SmartContentType.TOP_LIST: SmartPrompt(
            content_type=SmartContentType.TOP_LIST,
            prompt=(
                "This tweet contains or links to a list/ranking.\n\n"
                "Tweet: {tweet_text}\n{link_content}\n{image_analysis}\n\n"
                "TASK: Extract THE COMPLETE LIST with details.\n"
                "Provide:\n"
                "1. **List Title**: What is being ranked/listed?\n"
                "2. **Full List**: Every item with brief explanation (numbered)\n"
                "3. **Source/Author**: Who created this ranking?\n"
                "4. **Key Insight**: What's the most surprising or valuable item?\n\n"
                "The user saved this to reference the list later - GIVE THEM THE LIST."
            ),
            system_prompt="You are an expert at extracting and organizing lists. Extract every item with relevant details.",
            expected_output="Complete numbered list with descriptions",
        ),
        SmartContentType.TUTORIAL_GUIDE: SmartPrompt(
            content_type=SmartContentType.TUTORIAL_GUIDE,
            prompt=(
                "This tweet contains a tutorial, guide, or best practices.\n\n"
                "Tweet: {tweet_text}\n{link_content}\n{image_analysis}\n\n"
                "TASK: Extract ACTIONABLE STEPS the user can follow.\n"
                "Provide:\n"
                "1. **Goal**: What does this guide help you achieve?\n"
                "2. **Prerequisites**: What do you need before starting?\n"
                "3. **Steps**: Numbered, actionable steps (be specific!)\n"
                "4. **Key Tips**: Important warnings or pro tips\n"
                "5. **Example**: A concrete example if available\n\n"
                "The user saved this to learn HOW TO DO something - TEACH THEM."
            ),
            system_prompt="You are a technical educator. Extract clear, actionable instructions that someone can follow.",
            expected_output="Step-by-step guide with clear instructions",
        ),
        SmartContentType.TOOL_ANNOUNCEMENT: SmartPrompt(
            content_type=SmartContentType.TOOL_ANNOUNCEMENT,
            prompt=(
                "This tweet announces or discusses a tool/library/product.\n\n"
                "Tweet: {tweet_text}\n{link_content}\n{image_analysis}\n\n"
                "TASK: Extract PRACTICAL INFORMATION about this tool.\n"
                "Provide:\n"
                "1. **What It Is**: One-sentence description\n"
                "2. **Problem It Solves**: Why would someone use this?\n"
                "3. **Key Features**: Main capabilities (bullet points)\n"
                "4. **Installation**: How to get started (command or link)\n"
                "5. **When to Use**: Specific use cases\n\n"
                "The user saved this to potentially USE this tool - HELP THEM GET STARTED."
            ),
            system_prompt="You are a developer advocate. Explain tools in practical, actionable terms.",
            expected_output="Tool overview with installation and use cases",
        ),
        SmartContentType.CODE_SNIPPET: SmartPrompt(
            content_type=SmartContentType.CODE_SNIPPET,
            prompt=(
                "This tweet contains code, prompts, or technical snippets.\n\n"
                "Tweet: {tweet_text}\n{link_content}\n{image_analysis}\n\n"
                "TASK: Extract the COMPLETE CODE/PROMPT that the user wanted to save.\n"
                "Provide:\n"
                "1. **Purpose**: What does this code/prompt do?\n"
                "2. **Full Code/Prompt**: The complete, copy-pasteable content (in code block)\n"
                "3. **How to Use**: Instructions for using it\n"
                "4. **Customization**: What parts can/should be modified?\n\n"
                "The user saved this to USE THIS CODE/PROMPT LATER - GIVE IT TO THEM COMPLETE."
            ),
            system_prompt="You are a code expert. Extract and format code/prompts for easy copy-pasting.",
            expected_output="Complete code/prompt with usage instructions",
        ),
        SmartContentType.VIDEO_CONTENT: SmartPrompt(
            content_type=SmartContentType.VIDEO_CONTENT,
            prompt=(
                "This tweet contains a video.\n\n"
                "Tweet: {tweet_text}\n{video_analysis}\n\n"
                "TASK: Extract KEY INFORMATION from the video content.\n"
                "Provide:\n"
                "1. **Video Summary**: What happens in the video? (2-3 sentences)\n"
                "2. **Key Moments**: Important points or demonstrations shown\n"
                "3. **Main Takeaway**: What should the viewer remember?\n"
                "4. **Action Items**: What can someone do after watching?\n\n"
                "The user saved this video for a reason - CAPTURE WHY IT'S VALUABLE."
            ),
            system_prompt="You are an expert at video analysis. Extract the essential information someone would want to remember.",
            expected_output="Video summary with key moments and takeaways",
        ),
        SmartContentType.SCREENSHOT_INFO: SmartPrompt(
            content_type=SmartContentType.SCREENSHOT_INFO,
            prompt=(
                "This tweet contains screenshot(s) with information.\n\n"
                "Tweet: {tweet_text}\n{image_analysis}\n\n"
                "TASK: Extract ALL VISIBLE TEXT AND INFORMATION from the screenshot(s).\n"
                "Provide:\n"
                "1. **What It Shows**: What is in the screenshot?\n"
                "2. **Text Content**: Transcribe any visible text\n"
                "3. **Key Information**: What's the important data or insight?\n"
                "4. **Context**: How does the tweet text relate to the screenshot?\n\n"
                "The user saved this for the INFORMATION IN THE IMAGE - EXTRACT IT ALL."
            ),
            system_prompt="You are an OCR and image analysis expert. Extract every piece of useful text and information from images.",
            expected_output="Complete transcription of visible text and information",
        ),
        SmartContentType.OPINION_TAKE: SmartPrompt(
            content_type=SmartContentType.OPINION_TAKE,
            prompt=(
                "This tweet expresses an opinion or perspective.\n\n"
                "Tweet: {tweet_text}\nAuthor: @{author}\nEngagement: {likes} likes\n\n"
                "TASK: Capture the ESSENCE of this perspective.\n"
                "Provide:\n"
                "1. **The Take**: What is the author arguing? (1-2 sentences)\n"
                "2. **Supporting Points**: How do they support their argument?\n"
                "3. **Why It Matters**: Why might this perspective be valuable?\n"
                "4. **Counter-View**: What's the opposing perspective?\n\n"
                "The user saved this opinion for a reason - CAPTURE THE INSIGHT."
            ),
            system_prompt="You are a critical thinker. Capture opinions clearly while noting different perspectives.",
            expected_output="Clear summary of the opinion with context",
        ),
        SmartContentType.NEWS_UPDATE: SmartPrompt(
            content_type=SmartContentType.NEWS_UPDATE,
            prompt=(
                "This tweet contains news or an announcement.\n\n"
                "Tweet: {tweet_text}\n{link_content}\n\n"
                "TASK: Extract the NEWS FACTS.\n"
                "Provide:\n"
                "1. **What Happened**: The key news in 1-2 sentences\n"
                "2. **Who/What**: Key entities involved\n"
                "3. **When**: When did this happen?\n"
                "4. **Impact**: Why does this matter? Who does it affect?\n"
                "5. **Source**: Where is this information from?\n\n"
                "The user saved this news - GIVE THEM THE FACTS."
            ),
            system_prompt="You are a journalist. Extract facts clearly and accurately.",
            expected_output="News summary with key facts and impact",
        ),
        SmartContentType.UNKNOWN: SmartPrompt(
            content_type=SmartContentType.UNKNOWN,
            prompt=(
                "Analyze this tweet and extract maximum value.\n\n"
                "Tweet: {tweet_text}\nAuthor: @{author}\n{link_content}\n{image_analysis}\n\n"
                "TASK: Determine WHY the user might have saved this and extract that value.\n"
                "Consider:\n"
                "- Is there information to extract (lists, steps, code)?\n"
                "- Is there a link with valuable content?\n"
                "- Is there an image with information?\n"
                "- Is it an insight or perspective worth remembering?\n\n"
                "Provide the most USEFUL summary based on what this tweet contains."
            ),
            system_prompt="You are an expert at extracting value from content. Focus on actionable, specific information.",
            expected_output="Comprehensive analysis based on content type",
        ),
    }

    @classmethod
    def detect_content_type(
        cls,
        text: str,
        *,
        has_video: bool = False,
        has_image: bool = False,
        has_link: bool = False,
    ) -> SmartContentType:
        """Detect smart content type from bookmark text using regex (zero LLM cost).

        Args:
            text: Tweet/bookmark text
            has_video: Whether the bookmark has video content
            has_image: Whether the bookmark has images
            has_link: Whether the bookmark has external links

        Returns:
            Detected SmartContentType
        """
        if has_video:
            return SmartContentType.VIDEO_CONTENT

        text_lower = text.lower()

        # Check patterns in priority order
        for content_type, patterns in CONTENT_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    return content_type

        # Image with short text suggests screenshot info
        if has_image and len(text) < 100:
            return SmartContentType.SCREENSHOT_INFO

        # Link suggests article
        if has_link:
            return SmartContentType.ARTICLE_LINK

        return SmartContentType.UNKNOWN

    @classmethod
    def get_prompt(cls, content_type: SmartContentType) -> SmartPrompt:
        """Get the prompt template for a content type."""
        return cls.PROMPTS.get(content_type, cls.PROMPTS[SmartContentType.UNKNOWN])

    @classmethod
    def build_prompt(
        cls,
        text: str,
        author: str = "unknown",
        likes: int = 0,
        *,
        has_video: bool = False,
        has_image: bool = False,
        has_link: bool = False,
        link_content: Optional[str] = None,
        image_analysis: Optional[str] = None,
        video_analysis: Optional[str] = None,
    ) -> tuple[str, str]:
        """Build a tailored prompt for the given bookmark content.

        Args:
            text: Tweet/bookmark text
            author: Author username
            likes: Engagement count
            has_video: Whether the bookmark has video
            has_image: Whether the bookmark has images
            has_link: Whether the bookmark has external links
            link_content: Pre-fetched article content (optional)
            image_analysis: Vision model output for images (optional)
            video_analysis: Video analysis output (optional)

        Returns:
            Tuple of (formatted_prompt, system_prompt)
        """
        content_type = cls.detect_content_type(
            text,
            has_video=has_video,
            has_image=has_image,
            has_link=has_link,
        )
        smart_prompt = cls.get_prompt(content_type)

        subs = {
            "tweet_text": text,
            "author": author,
            "likes": likes,
            "link_content": "",
            "image_analysis": "",
            "video_analysis": "",
        }

        if link_content:
            subs["link_content"] = f"\n---\nLinked Content:\n{link_content}\n---"
        if image_analysis:
            subs["image_analysis"] = f"\n---\nImage Content:\n{image_analysis}\n---"
        if video_analysis:
            subs["video_analysis"] = f"\n---\nVideo Content:\n{video_analysis}\n---"

        prompt = smart_prompt.prompt.format(**subs)
        return prompt, smart_prompt.system_prompt

    @classmethod
    def describe_type(cls, content_type: SmartContentType) -> str:
        """Get a human-readable description of a content type."""
        descriptions = {
            SmartContentType.ARTICLE_LINK: "Tweet links to an article or blog post",
            SmartContentType.TOP_LIST: "Tweet contains or links to a list/ranking",
            SmartContentType.TUTORIAL_GUIDE: "Tweet contains a how-to or guide",
            SmartContentType.TOOL_ANNOUNCEMENT: "Tweet announces a tool or library",
            SmartContentType.CODE_SNIPPET: "Tweet contains code or prompts",
            SmartContentType.OPINION_TAKE: "Tweet expresses an opinion",
            SmartContentType.NEWS_UPDATE: "Tweet contains news",
            SmartContentType.THREAD_CONTENT: "Tweet is part of a thread",
            SmartContentType.VIDEO_CONTENT: "Tweet contains a video",
            SmartContentType.SCREENSHOT_INFO: "Tweet contains screenshot with info",
            SmartContentType.MEME_HUMOR: "Tweet is humorous/meme content",
            SmartContentType.UNKNOWN: "Content type not detected",
        }
        return descriptions.get(content_type, "Unknown content type")
