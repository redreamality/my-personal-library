# Verification record

## Live local run (2026-10-01 UTC)

The real reference article at `https://nekonull.me/posts/llm_x_bookmark/`
was processed, not replaced with a fixture:

- Jina Reader encountered a transport failure; direct extraction succeeded.
- The archive retained 5,137 characters of extracted text.
- The configured Qwen service returned validated bullets and a one-sentence
  summary with full coverage for this input.
- Searching for the Chinese term for bookmarks returned this archived entry.
- A private UTC ISO-week digest was generated.
- Replaying the same URL with no Qwen credential attempted zero new items and
  skipped one successful item.
- The explicit Wayback request returned `unavailable`. No independent public
  snapshot is claimed.

The private archive received the output. Public source README was not modified.
Historical source parsing found 114 unique admitted bookmarks, three duplicate
links, and one rejected relative URL. These are parser counts, not a claim
that all historical pages were fetched successfully.

## Regression findings

The initial live Windows run exposed SQLite handles remaining open after a
transaction context exited. Explicit connection closure and a regression test
now cover immediate index replacement and post-search renaming.

Raw snapshots retain original whitespace and line endings. The archive disables
Git conversion for raw Markdown; tests cover CRLF preservation through
checkpoint recovery, indexing, and replay. Generated weekly digests end with
exactly one newline.

Independent review also required distinguishing actual Reader body content from
its metadata, rejecting long challenge pages, redacting credentials after JSON
decoding, and reserving a timeout window for pushing partial results.

## Evidence boundaries

Offline tests exercise parser, HTTP/LLM fixtures, archive checkpoints, search,
weekly output, failures, and replay. They do not prove that every publisher is
accessible or that generated summaries are factually perfect.

The successful real local run proves one live extraction/summary/archive path.
Remote verification for commit `b813df0d97c11f3d403917962ce2c0cc04a16b1b`:

- [Linux tests](https://github.com/redreamality/my-personal-library/actions/runs/36884832788):
  all 39 tests passed, including actual POSIX SIGTERM delivery and lock release.
- [Initial archive workflow](https://github.com/redreamality/my-personal-library/actions/runs/36884832807):
  all steps completed, including weekly generation and authenticated private
  archive push. Of 12 attempts, six succeeded and six retained explicit Qwen
  failures; 101 unseen bookmarks remained after the batch.
- The private archive advanced to
  `896833449833161c3e38d9ca974174d1a907ac20`. Its seven successful entries include
  the earlier local reference-article smoke test.

The initial Qwen error codes did not contain HTTP status codes. A follow-up
adds numeric stage/status diagnostics without response bodies or automatic
retries, so subsequent operator-triggered retries can distinguish provider
errors. Three local health probes then returned HTTP 200; that does not explain
or erase the earlier runner-side failures.

Wayback and website availability remain external dependencies.

## Final deployment checks

For `4055d6b69fd1c307708a85cbb494084be4e74e47`,
[Linux CI](https://github.com/redreamality/my-personal-library/actions/runs/36885901303)
passed all 40 tests.
[The next batch](https://github.com/redreamality/my-personal-library/actions/runs/36885901465)
and [bounded retry batch](https://github.com/redreamality/my-personal-library/actions/runs/36885935447)
completed with private checkpoint pushes. The original six Qwen failures
recovered by reusing their saved extractions.

A four-item retry
([run](https://github.com/redreamality/my-personal-library/actions/runs/36887281798))
correctly failed its sync step when all four remaining items failed, but still
generated the digest and pushed the failure checkpoints. This deliberately
tests the failure path; it is not a successful extraction run.

One final one-item attempt, within the default three-attempt cap,
[succeeded](https://github.com/redreamality/my-personal-library/actions/runs/36887651078).
The private archive reached `c65bf0469995cd63774dc45f3164a7496b715b74`:

- 26 successfully archived and summarized bookmarks.
- Three source-access failures: HTTP 402, HTTP 403, and a transport failure.
- 85 unseen historical bookmarks left for the deployed hourly batches.
- No remaining Qwen failure among the attempted entries. This is a snapshot,
  not a promise that the external service will never fail again.

The three-language project introduction was deployed in the blog repository at
`b75c29448f11aca64338afb98597368dc5c402f0`.
[GitHub Pages deployment](https://github.com/redreamality/redreamality.github.io/actions/runs/36886963041)
succeeded. Local build, 170 unit tests, 19 focused browser tests, and all 181
site-wide browser tests passed. Mobile screenshots at 320 and 390 pixels were
inspected. Production EN, ZH and JA project URLs each returned HTTP 200 with
exactly one H1. Both implementation areas passed independent read-only review.

## Command incidents

- The archive bootstrap push timed out once and succeeded on a bounded retry.
  Source pushes later repeatedly timed out or reset while GitHub's API remained
  reachable. Git Data API transferred the exact tested tree and commit
  (hash equality checked), performed a non-forced ref update, and verified the
  remote SHA. Credentials, repository visibility, and commit history were not
  changed to work around the transport failure.
- SQLite atomic replacement failed on Windows because its connection was still
  open; fixed with explicit closure and tests.
- A raw-snapshot whitespace check failed because the publisher's extracted text
  contains trailing whitespace. Raw bytes were preserved with scoped Git
  attributes; only generated digest formatting was corrected.
- Optional directory probes used nonexistent guessed paths during investigation.
  Subsequent probes check existence or discover paths from the repository root.
- A new mobile test used an ambiguous `main` locator against the site's existing
  nested main elements. It was scoped to `body > main`; the same overflow
  assertions and screenshots then passed in all three locales.
- The bounded all-failed retry returned exit 1 by design. Logs and state retain
  safe error codes, while the separate persistence step still succeeded. Do not
  treat that red run as data loss or conceal it with an unconditional success.
- A later private-archive Git fetch also failed at the HTTPS connection stage.
  Exact Git objects can be read through GitHub's authenticated API and verified
  by object hash before a normal local fast-forward; never bypass TLS or
  overwrite a dirty checkout to work around this network condition.

No project incident-recording utility existed at task start. No one-off failure
was appended to persistent agent instructions.
