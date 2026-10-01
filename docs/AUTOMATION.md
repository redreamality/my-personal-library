# Running the deployed system

The existing `README.md` remains the osmos::memo bookmark input. Continue saving
bookmarks with the extension; no extension reconfiguration is required.
Implementation details and CLI commands are in [SYSTEM.md](SYSTEM.md).

## Repository boundaries

- `redreamality/my-personal-library`: existing public bookmarks, processing code,
  tests, and workflows.
- `redreamality/my-personal-library-archive`: private extracted Markdown,
  summaries, state, search index, and weekly digests. No GitHub Pages deployment.

The archive repository is initialized with `data.json`:

```json
{"version": 1, "entries": {}}
```

Do not point the pipeline at the source checkout or an unrelated directory.
Initialization refuses to overwrite an unmanaged, nonempty README.
The deployed archive also contains `.gitattributes` that disables line-ending
conversion and whitespace lint for `*_raw.md` snapshots. Keep that file: raw
text is evidence and must not be reformatted to satisfy a Markdown linter.

## Automation

The **Archive bookmarks** workflow runs when `README.md` or pipeline code changes
on `main`, every hour at minute 17 UTC, and through manual dispatch.
GitHub can delay scheduled workflows; the schedule is not an exact-time promise.
The default batch is 12 previously unprocessed bookmarks, newest first.

Each run tests the code, checks out the private archive, processes the batch,
writes the current UTC ISO-week digest, and commits its checkpoints to the
private archive. It never commits generated files to the source repository.
One workflow concurrency group serializes archive writes. Do not push local
archive edits while a workflow is running; synchronize the archive first.
Processing has a 75-minute process budget inside an 80-minute step and
90-minute job. This reserves time to push partial checkpoints. A hard runner
loss or manual cancellation can still lose changes since the last remote push.

Manual run:

```powershell
gh workflow run summarize.yml --repo redreamality/my-personal-library -f limit=12
```

The `limit` input accepts 1-100. Set `retry_failed=true` to retry previously
failed items, subject to the lifetime attempt cap. Set `wayback=true` to opt into
public Wayback Save Page Now requests; this is disabled by default and is not
required for private Markdown archiving.

```powershell
gh workflow run summarize.yml --repo redreamality/my-personal-library -f limit=12 -f retry_failed=true
gh run list --repo redreamality/my-personal-library --workflow summarize.yml --limit 5
```

Inspect numeric sync counts in the run logs and per-entry status in the private
`data.json`. Mixed-success batches preserve failures and report their count;
a green run does not mean every historical bookmark was archived.
Previously failed links are not silently retried every hour. Successful links
are skipped without another model call. The weekly digest is a private
Markdown file, not a public release or an email.

## Credentials

The source repository has two Actions secrets:

- `QWEN_API_KEY`: the credential for the fixed qwen-task service.
- `ARCHIVE_DEPLOY_KEY`: an SSH deploy key with write permission only on the
  private archive repository.

No personal GitHub token is installed in the workflow. Dependencies and tests
run before archive credentials are used. Checkout does not persist the deploy
key; the final push step installs it temporarily, pins GitHub's SSH host key,
and removes the temporary files on exit. Same-runner execution is not a sandbox
against malicious repository code: review changes before merging them to main.
Pull-request tests do not receive either secret.

Rotate the API secret through GitHub's secret settings. Rotate the deploy key by
creating a new archive-scoped key, replacing `ARCHIVE_DEPLOY_KEY`, confirming a
successful run, then removing the old key from the private repository.
Never paste a credential into README, a workflow input, command arguments, or
a workflow log.

## Search and read

Clone the private archive using your own GitHub account, then run from this code
checkout:

```powershell
git clone https://github.com/redreamality/my-personal-library-archive.git ../my-personal-library-archive
uv sync --locked
uv run python -m personal_library search --output ../my-personal-library-archive --query "SQLite"
```

Read the generated archive README, the linked summaries, and corresponding
`_raw.md` files. Search is local and requires no Qwen credential. It is keyword
and substring retrieval, not a chat interface or vector search.

## Known external limits

Sites can refuse access, require login, depend on JavaScript, disappear, or
return incomplete text. The pipeline records failures rather than inventing
summaries. Reader and Wayback availability are independent of local extraction:
a direct-extraction fallback can succeed while Wayback remains unavailable.
Long-document summaries explicitly state their coverage; full extracted text
is retained independently of that summary budget.
