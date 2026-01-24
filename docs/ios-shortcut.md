# iOS Shortcut Setup

This guide explains how to create an iOS Shortcut that sends Twitter/X bookmarks to your processor.

## Prerequisites

- Webhook server running and accessible (default port: 8766)
- Bearer token configured in `TWITTER_WEBHOOK_TOKEN` (optional but recommended)
- iOS 14+ or iPadOS 14+

## Creating the Shortcut

### Step 1: Create New Shortcut

1. Open the **Shortcuts** app on your iPhone/iPad
2. Tap **+** to create a new shortcut
3. Name it "Save Tweet" or similar

### Step 2: Add "Get URL from Share Sheet"

1. Tap **Add Action**
2. Search for "Receive"
3. Select **Receive input from Share Sheet**
4. Tap on "Images and 18 more" and select only **URLs**

### Step 3: Add "Get Contents of URL"

1. Tap **+** below the previous action
2. Search for "Get Contents"
3. Select **Get Contents of URL**
4. Configure the action:

   - **URL**: `http://YOUR_SERVER_IP:8766/process`
   - Tap **Show More** to expand options
   - **Method**: `POST`
   - **Headers**: Add a new header
     - **Key**: `Authorization`
     - **Value**: `Bearer YOUR_TOKEN_HERE`
   - **Request Body**: `JSON`
   - **JSON Body**: Add a new field
     - **Key**: `url`
     - **Value**: Select **Shortcut Input** from the variables

### Step 4: (Optional) Show Notification

1. Tap **+** to add another action
2. Search for "Show Notification"
3. Select **Show Notification**
4. Set the message to "Tweet saved!" or use the response from the previous action

## Example Configuration

```
URL: http://100.66.201.114:8766/process
Method: POST
Headers:
  Authorization: Bearer my-secret-token-123
Request Body: JSON
  {
    "url": [Shortcut Input]
  }
```

## Using the Shortcut

1. Open Twitter/X app
2. Find a tweet you want to save
3. Tap the **Share** button
4. Select your "Save Tweet" shortcut from the share sheet
5. The shortcut will send the URL to your server for processing

## Server Response

On success, the server returns HTTP 202 with:

```json
{
  "status": "accepted",
  "url": "https://x.com/user/status/123456",
  "task_id": "abc12345",
  "tweet_id": "123456"
}
```

Processing happens asynchronously - the note will appear in your Obsidian vault shortly after.

## Troubleshooting

### "Unauthorized" Error

- Verify your bearer token matches `TWITTER_WEBHOOK_TOKEN` on the server
- Check the Authorization header format: `Bearer <token>` (with space after Bearer)

### "Invalid URL" Error

- The shortcut only works with Twitter/X status URLs
- Make sure you're sharing from a tweet, not a profile or search

### Connection Timeout

- Verify the server is running: `curl http://YOUR_SERVER_IP:8766/health`
- Check your firewall allows connections on port 8766
- If using Tailscale, ensure both devices are on the same network

### Server Not Accessible

- The server must be reachable from your iOS device
- If running locally, use your Mac's IP address (not localhost)
- Consider using Tailscale for secure remote access
