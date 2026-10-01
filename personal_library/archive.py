"""Durable JSON authority with rebuildable Markdown and SQLite projections."""

from contextlib import closing
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .qwen import validate_summary
from .security import PipelineError, canonical_url


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_raw(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def plain(value: str) -> str:
    # No scraped HTML, image embeds, or active Markdown from model output.
    value = " ".join(value.split())
    for character in "\\`*_{}[]<>!#|":
        value = value.replace(character, "\\" + character)
    return value


def summary_markdown(entry: dict) -> str:
    coverage = entry["coverage"]
    label = "full extracted text" if coverage["complete"] else "PARTIAL PREFIX ONLY"
    text = (
        f"# {plain(entry['title'])}\n\n"
        f"Source: <{entry['url']}>\n\n"
        f"Coverage: **{label}** "
        f"({coverage['summarized_chars']}/{coverage['total_chars']} characters).\n\n"
        f"{plain(entry['summary']['sentence'])}\n\n"
    )
    for bullet in entry["summary"]["bullets"]:
        text += f"- **{plain(bullet['heading'])}**\n"
        for detail in bullet["details"]:
            text += f"  - {plain(detail)}\n"
    return text


class Archive:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state = {"version": 1, "entries": {}}
        self.lock_fd = None

    def path(self, relative: str) -> Path:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise PipelineError("archive_path_unsafe")
        cursor = path
        while cursor != self.root:
            if cursor.is_symlink():
                raise PipelineError("archive_path_unsafe")
            cursor = cursor.parent
        return path

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_fd = os.open(
                self.path(".personal-library.lock"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            raise PipelineError("archive_locked") from None
        try:
            os.write(self.lock_fd, str(os.getpid()).encode("ascii"))
            state_path = self.path("data.json")
            if state_path.exists():
                self.state = json.loads(state_path.read_text(encoding="utf-8"))
                self.validate()
            elif self.path("README.md").exists() and self.path("README.md").stat().st_size:
                raise PipelineError("archive_unmanaged_readme")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
            self.path(".personal-library.lock").unlink(missing_ok=True)

    def validate(self):
        try:
            if self.state["version"] != 1 or not isinstance(self.state["entries"], dict):
                raise ValueError()
            for identity, entry in self.state["entries"].items():
                if not re.fullmatch(r"[a-f0-9]{64}", identity):
                    raise ValueError()
                if canonical_url(entry["url"]) != entry["url"]:
                    raise ValueError()
                if hashlib.sha256(entry["url"].encode()).hexdigest() != identity:
                    raise ValueError()
                if not re.fullmatch(r"\d{6}", entry["month"]):
                    raise ValueError()
                date.fromisoformat(entry["created_at"][:10])
                if not isinstance(entry["attempts"], int) or entry["attempts"] < 0:
                    raise ValueError()
                if entry["status"] not in {"running", "failed", "success"}:
                    raise ValueError()
                if entry["status"] == "success":
                    validate_summary(entry["summary"])
                    coverage = entry["coverage"]
                    if not isinstance(coverage["complete"], bool):
                        raise ValueError()
                    if not 0 < coverage["summarized_chars"] <= coverage["total_chars"]:
                        raise ValueError()
                    if coverage["complete"] != (
                        coverage["summarized_chars"] == coverage["total_chars"]
                    ):
                        raise ValueError()
        except (KeyError, ValueError, TypeError, PipelineError):
            raise PipelineError("archive_state_invalid") from None

    def checkpoint(self):
        atomic_write(self.path("data.json"), json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")

    def raw_path(self, identity: str, entry: dict) -> Path:
        return self.path(f"{entry['month']}/{identity}_raw.md")

    def successes(self):
        return sorted(
            ((identity, entry) for identity, entry in self.state["entries"].items()
             if entry["status"] == "success"),
            key=lambda pair: (pair[1]["created_at"], pair[0]), reverse=True,
        )

    def rebuild(self):
        rows = []
        index = "# Personal Library\n\nGenerated private archive. Source bookmarks are not modified.\n\n"
        for identity, entry in self.successes():
            raw_path = self.raw_path(identity, entry)
            if not raw_path.exists():
                raise PipelineError("archive_raw_missing")
            raw = read_raw(raw_path)
            if hashlib.sha256(raw.encode()).hexdigest() != entry["raw_sha256"]:
                raise PipelineError("archive_raw_changed")
            relative = f"{entry['month']}/{identity}.md"
            atomic_write(self.path(relative), summary_markdown(entry))
            partial = " [PARTIAL]" if not entry["coverage"]["complete"] else ""
            index += (
                f"- [{plain(entry['title'])}]({relative}){partial}: "
                f"{plain(entry['summary']['sentence'])}\n"
            )
            rows.append((
                identity, entry["title"], entry["url"],
                json.dumps(entry["summary"], ensure_ascii=False), raw,
            ))
        atomic_write(self.path("README.md"), index)
        target = self.path("search.sqlite3")
        fd, temporary = tempfile.mkstemp(prefix=".search-", suffix=".sqlite3", dir=self.root)
        os.close(fd)
        try:
            with closing(sqlite3.connect(temporary)) as db, db:
                db.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, title TEXT, url TEXT, summary TEXT, body TEXT)")
                db.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?)", rows)
                db.execute("CREATE VIRTUAL TABLE search_unicode USING fts5(id UNINDEXED, title, summary, body, tokenize='unicode61')")
                db.execute("INSERT INTO search_unicode SELECT id, title, summary, body FROM documents")
                try:
                    db.execute("CREATE VIRTUAL TABLE search_trigram USING fts5(id UNINDEXED, title, summary, body, tokenize='trigram')")
                    db.execute("INSERT INTO search_trigram SELECT id, title, summary, body FROM documents")
                except sqlite3.OperationalError as error:
                    if "no such tokenizer" not in str(error):
                        raise
                    # Older SQLite supports unicode FTS5 but lacks trigram.
                db.commit()
            os.replace(temporary, target)
        except sqlite3.OperationalError:
            raise PipelineError("sqlite_fts_unavailable") from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        if not query.strip() or len(query) > 500 or not 1 <= limit <= 100:
            raise PipelineError("invalid_search")
        self.rebuild()
        phrase = '"' + query.replace('"', '""') + '"'
        with closing(sqlite3.connect(self.path("search.sqlite3"))) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
            matches = []
            for table in ("search_unicode", "search_trigram"):
                if table in tables:
                    matches.extend(row[0] for row in db.execute(
                        f"SELECT id FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT ?",
                        (phrase, limit),
                    ))
            # One/two-character Chinese queries and unavailable tokenizers use
            # literal substring search, not SQL wildcard or FTS query syntax.
            matches.extend(row[0] for row in db.execute(
                "SELECT id FROM documents WHERE instr(lower(title || char(10) || summary || char(10) || body), lower(?)) > 0 ORDER BY id LIMIT ?",
                (query, limit),
            ))
            result = []
            for identity in dict.fromkeys(matches):
                row = db.execute("SELECT id, title, url FROM documents WHERE id=?", (identity,)).fetchone()
                result.append(dict(zip(("id", "title", "url"), row)))
                if len(result) == limit:
                    break
            return result

    def weekly(self, week: str) -> str:
        try:
            if not re.fullmatch(r"\d{4}-W\d{2}", week):
                raise ValueError()
            year, number = int(week[:4]), int(week[-2:])
            date.fromisocalendar(year, number, 1)
        except ValueError:
            raise PipelineError("invalid_week") from None
        text = f"# Bookmark Digest {week}\n\n"
        for identity, entry in self.successes():
            created = date.fromisoformat(entry["created_at"][:10]).isocalendar()
            if (created.year, created.week) != (year, number):
                continue
            partial = " [PARTIAL]" if not entry["coverage"]["complete"] else ""
            text += (
                f"## {plain(entry['title'])}{partial}\n\n"
                f"{plain(entry['summary']['sentence'])}\n\n"
                f"Summary: ../{entry['month']}/{identity}.md\n\n"
            )
            for bullet in entry["summary"]["bullets"]:
                text += f"- {plain(bullet['heading'])}: " + " ".join(
                    plain(detail) for detail in bullet["details"]
                ) + "\n"
            text += "\n"
        return text.rstrip("\n") + "\n"
