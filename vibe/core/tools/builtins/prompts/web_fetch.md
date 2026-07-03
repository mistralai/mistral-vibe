Use `web_fetch` to fetch content from a URL.

Usage:
- Takes a `url` and returns the page content converted to markdown for readability.
- Use this tool when you need to retrieve and analyze web content.
- Prefer a more specialized tool over `web_fetch` when one is available.
- The URL must be fully-formed and valid; HTTP URLs are upgraded to HTTPS.
- This tool is read-only and does not modify any files.
- Content is capped at a byte limit; if `was_truncated` is true, the page had more content that was cut off.
