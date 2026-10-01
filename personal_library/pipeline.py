"""Bounded sequential execution with a checkpoint before and after every item."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .archive import Archive, atomic_write, read_raw
from .fetch import Fetcher
from .qwen import Qwen
from .security import PipelineError, canonical_url
from .source import Bookmark, parse_bookmarks


def sync(
    source: Path, output: Path, *, limit: int = 10, url: str | None = None,
    retry_failed: bool = False, max_attempts: int = 3, wayback: bool = False,
    fetcher=None, summarizer=None, now=None,
) -> dict[str, int]:
    if not 1 <= limit <= 100 or not 1 <= max_attempts <= 10:
        raise PipelineError("invalid_bounds")
    source, output = source.resolve(), output.resolve()
    if source.is_relative_to(output):
        raise PipelineError("output_contains_source")
    bookmarks, parsed = parse_bookmarks(source.read_text(encoding="utf-8-sig"))
    if url:
        selected = canonical_url(url)
        bookmarks = [bookmark for bookmark in bookmarks if bookmark.url == selected] or [
            Bookmark(hashlib.sha256(selected.encode()).hexdigest(), selected, selected)
        ]
    counts = {
        "found": len(bookmarks), **parsed, "attempted": 0, "succeeded": 0,
        "failed": 0, "skipped_success": 0, "skipped_failed": 0,
        "exhausted": 0, "deferred": 0, "wayback_saved": 0, "wayback_submitted": 0,
        "wayback_existing": 0, "wayback_unavailable": 0,
    }
    fetcher, summarizer = fetcher or Fetcher(), summarizer or Qwen()
    now = now or (lambda: datetime.now(timezone.utc))
    with Archive(output) as archive:
        archive.checkpoint()
        archive.rebuild()
        for bookmark in bookmarks:
            entry = archive.state["entries"].get(bookmark.id)
            if entry and entry["status"] == "success":
                counts["skipped_success"] += 1
                continue
            if entry and entry["attempts"] >= max_attempts:
                counts["exhausted"] += 1
                continue
            if entry and entry["status"] == "failed" and not retry_failed:
                counts["skipped_failed"] += 1
                continue
            if counts["attempted"] >= limit:
                counts["deferred"] += 1
                continue
            timestamp = now().astimezone(timezone.utc)
            if entry is None:
                entry = {
                    "url": bookmark.url, "title": bookmark.title,
                    "month": timestamp.strftime("%Y%m"),
                    "created_at": timestamp.isoformat(), "attempts": 0, "errors": [],
                }
                archive.state["entries"][bookmark.id] = entry
            entry.update(
                status="running", attempts=entry["attempts"] + 1,
                updated_at=timestamp.isoformat(),
            )
            archive.checkpoint()
            counts["attempted"] += 1
            try:
                # Reuse previously checkpointed extraction after LLM failure.
                raw_path = archive.raw_path(bookmark.id, entry)
                if entry.get("raw_sha256") and raw_path.exists():
                    text = read_raw(raw_path)
                    if hashlib.sha256(text.encode()).hexdigest() != entry["raw_sha256"]:
                        raise PipelineError("archive_raw_changed")
                else:
                    document = fetcher.fetch(bookmark.url)
                    text = document.text
                    atomic_write(raw_path, text)
                    entry.update(
                        raw_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        fetch={
                            "method": document.method, "final_url": document.final_url,
                            "reader_error": document.reader_error,
                        },
                    )
                    archive.checkpoint()
                summary, coverage = summarizer.summarize(bookmark.title, text)
                entry.update(summary=summary, coverage=coverage, status="success")
                if wayback:
                    entry["wayback"] = fetcher.wayback(bookmark.url)
                    counts["wayback_" + entry["wayback"]["status"]] += 1
                else:
                    entry["wayback"] = {"status": "not_requested"}
                counts["succeeded"] += 1
            except PipelineError as error:
                entry["status"] = "failed"
                entry["errors"].append({
                    "attempt": entry["attempts"], "at": timestamp.isoformat(), "code": error.code,
                })
                counts["failed"] += 1
            except Exception:
                entry["status"] = "failed"
                entry["errors"].append({
                    "attempt": entry["attempts"], "at": timestamp.isoformat(),
                    "code": "internal_item_error",
                })
                counts["failed"] += 1
            archive.checkpoint()
            archive.rebuild()
    return counts
