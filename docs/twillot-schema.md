# Twillot Export Schema

Documentation of the JSON schema exported by [Twillot](https://twillot.com) Chrome extension.

> **Source:** Analyzed from real export of 914 bookmarks (January 2026)

## Export Structure

### JSON Export

- **Format:** Array of tweet objects
- **File:** `twillot-bookmark.json`
- **Encoding:** UTF-8

### Media Export

- **Format:** Individual files organized by folder
- **Naming pattern:** `{screen_name}-{tweet_id}-{media_id}.{ext}`
- **Supported types:** `.jpg`, `.png`, `.mp4`, `.gif`
- **Example:** `mattpocockuk-2011092347254182097-G-jVy_kWUAA4TVK.png`

## Field Reference

### Identifiers

| Field | Type | Description |
|-------|------|-------------|
| `tweet_id` | string | Unique tweet ID (numeric string) |
| `conversation_id` | string | Thread root tweet ID (same as tweet_id if not in thread) |
| `user_id` | string | Author's user ID |
| `owner_id` | string | Bookmark owner's user ID |
| `id` | string | Composite ID: `bookmark_{owner_id}_{tweet_id}` |
| `sort_index` | string | Internal sorting index |

### Content

| Field | Type | Description |
|-------|------|-------------|
| `full_text` | string | Full tweet text (including URLs) |
| `media_items` | string[] | Array of media URLs (images/videos) |
| `lang` | string | ISO language code (`en`, `pt`, `es`, etc.) |
| `url` | string | Direct URL to tweet (optional) |

### Author Info

| Field | Type | Description |
|-------|------|-------------|
| `screen_name` | string | Twitter handle (without @) |
| `username` | string | Display name |
| `avatar_url` | string | Profile image URL |

### Engagement Metrics

| Field | Type | Description |
|-------|------|-------------|
| `views_count` | string | View count (note: string, not int) |
| `bookmark_count` | int | Bookmark count |
| `favorite_count` | int | Like count |
| `quote_count` | int | Quote tweet count |
| `reply_count` | int | Reply count |
| `retweet_count` | int | Retweet count |

### User Interaction Flags

| Field | Type | Description |
|-------|------|-------------|
| `bookmarked` | bool | User bookmarked this tweet |
| `favorited` | bool | User liked this tweet |
| `retweeted` | bool | User retweeted this tweet |

### Content Type Flags

| Field | Type | Description |
|-------|------|-------------|
| `has_image` | bool | Contains image(s) |
| `has_video` | bool | Contains video |
| `has_gif` | bool | Contains GIF |
| `has_link` | bool | Contains external URL |
| `is_long_text` | bool | Tweet exceeds 280 chars (Twitter Blue) |
| `is_thread` | null/bool | Part of a thread (always `null` in observed data) |
| `is_quote` | bool | Quote tweet |
| `is_reply` | bool | Reply to another tweet |
| `is_repost` | bool | Retweet (always `false` in bookmarks) |

### Metadata

| Field | Type | Description |
|-------|------|-------------|
| `created_at` | string | ISO 8601 timestamp (`2026-01-13T15:05:54.000Z`) |
| `folder` | string | Twillot folder name (default: `unsorted`) |
| `category_name` | string | Always `bookmark` for bookmark exports |
| `request_at` | int | Unix timestamp of export request |
| `possibly_sensitive` | bool | Flagged as sensitive content |
| `sensitive` | bool | Alternative sensitive flag (optional) |
| `post_type` | string | Type: `post` (optional) |

### Thread/Quote Data

| Field | Type | Description |
|-------|------|-------------|
| `conversations` | array | Thread tweets (always empty in observed data) |
| `quoted_tweet` | string | URL of quoted tweet (only if `is_quote=true`) |

### Raw API Data

| Field | Type | Description |
|-------|------|-------------|
| `_data` | object | Full Twitter API response (excluded from samples) |

## Content Distribution (Sample: 914 tweets)

```
With video:    269 (29%)
With image:    376 (41%)
With GIF:        2 (<1%)
With link:     162 (18%)
Quote tweets:  129 (14%)
Replies:        48 (5%)
Long text:     179 (20%)
```

## Languages Detected

`ca`, `en`, `es`, `ja`, `pt`, `qme`, `und`, `zxx`

## Known Limitations

1. **Thread detection:** `is_thread` is always `null`; threads not automatically detected
2. **Conversations:** `conversations` array is always empty
3. **Reposts:** `is_repost` always `false` (can't bookmark pure retweets)
4. **Views as string:** `views_count` is string, not integer

## Edge Cases

### Quote Tweets
- `is_quote: true`
- `quoted_tweet` contains URL to original tweet
- May also have `has_image: true` if quote adds media

### Replies
- `is_reply: true`
- `conversation_id` points to thread root

### Videos
- `has_video: true`
- `media_items` contains video URL(s)
- Media files exported as `.mp4`

### Long Text (Twitter Blue)
- `is_long_text: true`
- `full_text` contains complete text

## Sample Data

See `tests/fixtures/twillot_sample.json` for anonymized examples of each content type.
