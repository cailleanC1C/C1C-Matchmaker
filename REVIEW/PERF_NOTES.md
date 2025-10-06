# Performance Notes

- **Sheets access dominates latency.** `get_rows()` downloads the entire worksheet (~N×33 columns) via `get_all_values()` on every cache miss. Combined with F-01 (blocking calls), this can stall the bot for multiple seconds under moderate network jitter.
- **Thumbnail generation.** Member search builds emoji thumbnails for up to 10 clans per page via Pillow. These are fast once emojis are cached server-side but should be monitored when adding more concurrent searches.
- **Daily summary parsing.** `_locate_summary_headers` walks ~80 rows per refresh. Once an adapter exists this could be memoized with column indices to avoid repeated scans.
