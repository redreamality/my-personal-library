# Bookmark Pipeline

## Purpose and ownership

This implements the public-page extraction, structured summary, durable archive,
index, and weekly-digest pattern described in
[LLM x bookmarks: summary and full-text index](https://nekonull.me/posts/llm_x_bookmark/).
Unlike the reference's two summary calls, this pipeline requests bullets and one
sentence together in one structured Qwen call.

The source README is the osmos::memo input, not an output. It is never edited.
Run from the source checkout and point `--output` at a separate **private**
archive checkout. The package does not create repositories, deploy, commit,
push, obtain credentials, or configure GitHub Actions.

## Install and verify

Python 3.11 or later and SQLite with FTS5 are required.

```powershell
uv sync --locked
uv run python -m unittest discover -s tests -v
```

Tests are offline: public DNS, public HTTP and Qwen are replaced by local
fixtures. The end-to-end fixture uses the real parser, extraction/summary
clients, archive, SQLite search, CLI, weekly renderer, and replay logic.
Windows tests exercise immediate SQLite replacement and index renaming after
search. SQLite transaction context managers do not close connections; all
connections are explicitly closed before replacing files.

## CLI contract

Inject `QWEN_API_KEY` into the process environment through your secret manager or
Actions secret. Never put it on the command line. The package does not invoke
gopass, read `.env`, or discover other credentials.

First real smoke test (Wayback explicitly makes the URL public):

```powershell
uv run python -m personal_library sync --source README.md --output ../my-personal-library-archive --limit 1 --url https://nekonull.me/posts/llm_x_bookmark/ --wayback
```

Bounded normal batch, for a workflow with the archive checked out at `../archive`:

```powershell
uv run python -m personal_library sync --source README.md --output ../archive --limit 100
uv run python -m personal_library weekly --output ../archive --week 2026-W40 --write
uv run python -m personal_library search --output ../archive --query "knowledge" --limit 20
```

`sync` flags:

| Flag | Contract |
| --- | --- |
| `--source` | Markdown source, default `README.md` |
| `--output` | Required private archive directory |
| `--limit` | Attempt at most N entries; default 10, minimum 1, **maximum 100** |
| `--url` | Select this canonical URL; explicitly add it if not present in the source |
| `--retry-failed` | Permit another attempt for previously failed entries |
| `--max-attempts` | Lifetime per-entry attempt cap, default 3, range 1-10 |
| `--wayback` | Opt-in public Save Page Now submission, best effort |

Processing is sequential; there is no `--workers` flag. Scheduled invocations
skip successful and failed entries by default, so successive batches work
through older unseen bookmarks. A separate explicitly requested retry batch
can use `--retry-failed`; it still obeys the attempt cap.

`sync` stdout is a single JSON object of numeric counts: `found`, `rejected`,
`duplicates`, `attempted`, `succeeded`, `failed`, `skipped_success`,
`skipped_failed`, `exhausted`, `deferred`, `wayback_saved`,
`wayback_submitted`, `wayback_existing`, and `wayback_unavailable`.
No fetched body, summary, URL, title, key, or upstream error body is printed.
`found` counts unique admitted bookmarks after optional URL selection.
`rejected` and `duplicates` describe source parsing, even with `--url`.

Exit statuses:

- `0`: at least one attempted item succeeded, or no items were attempted.
- `1`: every attempted item failed. State and previous successes are retained.
- `2`: invalid arguments, corrupt archive, I/O failure, lock conflict, or another
  fatal error. Stderr is a fixed-code JSON error, never an upstream traceback.
- `124`: SIGTERM interrupted execution. The CLI unwinds the archive context,
  removes its lock, and leaves checkpointed `running` work resumable without
  turning termination into an ordinary per-entry failure or printing a traceback.

Mixed-success batches return 0 with a nonzero `failed` count. No-work batches
can have nonzero skipped/exhausted counts; 0 does not mean all history succeeded.
Automation should preserve/checkpoint the archive even when sync returns 1,
then surface the counts and exit status rather than claim total success.

`weekly --output DIR [--week YYYY-Www] [--write]` uses UTC ISO weeks; the
default is the current UTC week. **Use `--write` in automation**: it writes
`weekly/YYYY-Www.md` and prints only `{"written": 1}`. Without `--write`, the
private digest goes to stdout. Digests are deterministic from stored summaries,
require no network or LLM, and group by the entry's first attempt date.

`search --output DIR --query TEXT [--limit N]` returns a JSON array of
`id`, `title`, and `url` for successful archived entries. Its limit defaults to
20 and accepts 1-100. It deliberately prints private metadata; do not put search
results into public Actions logs.

## Parsing and URL admission

`markdown-it-py` parses top-level list links before the first top-level H1
`About` (case-insensitive). Nested-list links and links in the About section are
not bookmarks. Memo's malformed, unindented multiline labels are folded only
when they become a valid Markdown link; nested brackets and reference links
are supported. Relative URLs and non-HTTP(S) links are rejected.

IDs are full SHA-256 hex digests of canonical URLs. Canonicalization lowercases
scheme/host, normalizes host IDNA and default ports, supplies `/` for an empty
path, and removes fragments. **Queries retain parameter order, duplicates,
encoding, tracking parameters, and values.** This deliberately does not merge
distinct semantic queries or speculate about equivalent slash/path encodings.

URLs with embedded credentials, sensitive query names (tokens, passwords,
signatures, sessions, API keys, auth codes), or obvious credential values are
rejected before sending them to Jina, a publisher, or Wayback. Private/reserved
IP literals, local hostnames, non-public DNS answers, and nonstandard ports are
rejected. This is conservative screening, not a complete secret detector:
review source bookmarks for arbitrary private information in paths or queries.

## Fetching and trust

Primary extraction uses `https://r.jina.ai/<public-url>`. An unavailable or
invalid Reader response falls back to direct public HTML extraction through
trafilatura. The minimum text length applies after `Markdown Content:`, not to
Reader metadata. Known challenge/error titles and standalone opening notices
are checked regardless of body length, with punctuation and ellipses normalized;
ordinary prose discussing those phrases is not rejected. Reader errors, HTTP
errors, login/password forms, and detected challenge pages are not summarized.
There is no login, browser automation,
cookie jar reuse, captcha bypass, or authenticated publisher request.

Direct requests manually validate each redirect, resolve all addresses,
reject mixed public/private answers, and connect to the selected public IP.
The original Host, TLS SNI, and certificate verification remain enabled.
Fresh clients do not carry cookies, credentials, netrc, or environment proxies
between requests. Redirects are bounded to five hops.

Reader's server-side fetching is a third-party trust boundary: local code
cannot pin its DNS or inspect every internal redirect. Input and reported
`URL Source` are validated; this is not a claim that Reader's internal
network behavior is locally controlled.

Public response bodies are limited to 8 MB of decoded bytes; oversize documents
fail explicitly, never become silently truncated successful archives.
Public HTTP connect/read timeouts are 15/45 seconds. Qwen requests have a
300-second httpx timeout. These are transport inactivity timeouts, not hard
whole-process wall-clock deadlines; bound Actions runtime separately.

Raw files preserve the **entire extracted text** with no summary-input
truncation (including Reader's envelope). Direct mode stores full trafilatura
extraction, not original HTML, images, binaries, or a browser snapshot.
Extraction can omit navigation, comments, or JavaScript-only content. Raw
Markdown remains untrusted and can contain remote links; do not render it with
unrestricted HTML or automatically execute embedded commands.

## Qwen and coverage

Both `/health` and `/v1/chat/completions` use only
`https://qwen.redreamality.com`, `Authorization: Bearer $QWEN_API_KEY`, and
`User-Agent: qwen-task/1.1`. Model: `unsloth/Qwen3.8-27B-NVFP4`.
Both endpoints forbid redirects. No automatic API retry or fallback endpoint
is used. A health request precedes each summary call.

The system prompt treats title and scraped document as untrusted data.
The JSON response must contain `sentence` and 3-8 structured `bullets`, each
with a `heading` and 1-4 `details`. The client validates structure and string
bounds, rejects non-stop finish reasons, tool calls, refusals, and invalid JSON,
and consumes only `message.content`, never reasoning fields. A reflected
current API key is redacted both before JSON decoding and in every validated
sentence, heading, and detail string after decoding, including escaped keys.
Sentence brevity and factual
accuracy remain model-quality properties, not guarantees of the schema.

The summary input is at most 48,000 characters. Longer documents use an explicit
prefix strategy, not a full-document claim. `coverage` records `strategy`,
`complete`, `total_chars`, and `summarized_chars`. Summary Markdown, index, and
weekly digest prominently label partial summaries. Full raw text and full-text
search still include the tail. There is no map/reduce in this initial version.
The package does not set unverified `chat_template_kwargs`; hidden reasoning
may consume the 4,096 output-token budget. A truncated response fails safely
and retains the raw extraction for a later explicitly bounded retry.

## Archive and recovery

```text
data.json
README.md
search.sqlite3
YYYYMM/<sha256>_raw.md
YYYYMM/<sha256>.md
weekly/YYYY-Www.md
```

`data.json` is the versioned authority. Each entry records status, attempts,
timestamps, full-text hash, fetch method/fallback reason, structured summary,
coverage, safe error history, and Wayback status. A checkpoint is written
before attempting each item, after saving raw text, and after its outcome.
Writes use a same-directory temporary file, flush/fsync, then atomic replace.
This is per-file atomicity, not a multi-file transaction or a guarantee against
all power-loss/filesystem behavior.

`README.md`, summary files, and SQLite are rebuildable projections. Sync and
search rebuild them under an exclusive `.personal-library.lock`. Index
replacement happens after its connection closes. Successful entries are
idempotently skipped; missing projections are repaired without network calls.
Missing/changed raw data for a successful entry fails closed rather than
silently re-fetching or discarding history. This version rebuilds the complete
index after each item: appropriate for the initial hundreds of bookmarks,
not optimized for very large archives.

A model failure retains fetched raw text for a later retry. A gracefully
interrupted `running` entry resumes automatically within the attempt cap.
A hard process kill can leave the lock file: verify no writer remains, then
remove only that exact stale lock before restarting. Abandoned `.pending-*`
or `.search-*.sqlite3` files are not authoritative. Do not delete `data.json`
to retry a failure. Increasing `--max-attempts` is an explicit operator decision.

SQLite always builds unicode61 FTS5 and also trigram FTS when available.
Literal substring fallback covers one/two-character Chinese queries and
SQLite builds without trigram. No embeddings, external search service, or
LLM query call is involved.

## Wayback

Default is off. `--wayback` submits the admitted URL to
`https://web.archive.org/save/<URL>` without following service redirects.
Only a validated `Content-Location`/`Location` snapshot on exact
`web.archive.org`, with a timestamped replay path matching the submitted URL,
is recorded as `saved`. HTTP 202 without a confirmed snapshot is `submitted`,
not saved. On failure, an availability lookup may record `existing`; absence
or service failure is `unavailable`. An HTTP 200 without snapshot evidence is
not proof of archival success. Wayback failure does not invalidate a summary.

Opting in discloses the URL to a public archive. It applies only to entries
actually processed during that invocation; already-successful entries are
skipped and are not retroactively submitted.
