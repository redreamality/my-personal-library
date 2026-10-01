"""Extract top-level bookmark items without interpreting the About section."""

from dataclasses import dataclass
import hashlib
import re

from markdown_it import MarkdownIt

from .security import PipelineError, canonical_url


@dataclass(frozen=True)
class Bookmark:
    id: str
    url: str
    title: str


def parse_bookmarks(markdown: str) -> tuple[list[Bookmark], dict[str, int]]:
    parser = MarkdownIt("commonmark")
    environment = {}
    parser.parse(markdown, environment)
    lines = markdown.splitlines()
    # Memo can write unindented blank lines inside a link label, which is not
    # valid CommonMark. Join only a leading list link that becomes a real link
    # under the inline parser; stop at the next structural block.
    for start, line in enumerate(lines):
        if not re.match(r"^ {0,3}(?:[-+*]|\d+[.)])\s+\[", line):
            continue
        if any(t.type == "link_open" for t in parser.parseInline(line, environment)[0].children or []):
            continue
        candidate = line
        for end in range(start + 1, len(lines)):
            if re.match(r"^ {0,3}(?:[-+*]\s|\d+[.)]\s|#{1,6}\s|```|~~~|\[[^]]+\]:)", lines[end]):
                break
            candidate += " " + lines[end].strip()
            if any(t.type == "link_open" for t in parser.parseInline(candidate, environment)[0].children or []):
                lines[start] = candidate
                lines[start + 1:end + 1] = [""] * (end - start)
                break
    tokens = parser.parse("\n".join(lines), environment)
    boundary = len(lines)
    for index, token in enumerate(tokens):
        if (
            token.type == "heading_open" and token.tag == "h1" and token.level == 0
            and tokens[index + 1].content.strip().casefold() == "about"
        ):
            boundary = token.map[0]
            break
    found = {}
    counts = {"rejected": 0, "duplicates": 0}
    for index, token in enumerate(tokens):
        if token.type != "list_item_open" or token.level != 1 or token.map[0] >= boundary:
            continue
        start, end = token.map
        end = min(end, boundary)
        # Nested lists are not additional top-level bookmarks.
        for child in tokens[index + 1:]:
            if child.type == "list_item_close" and child.level == 1:
                break
            if child.type in {"bullet_list_open", "ordered_list_open"} and child.map:
                end = min(end, child.map[0])
        # Osmos memo labels can contain blank lines. Fold just this list item's
        # text before inline parsing; regex is not used to recognize links.
        text = " ".join(line.strip() for line in lines[start:end])
        text = re.sub(r"^(?:[-+*]|\d+[.)])\s+", "", text)
        children = parser.parseInline(text, environment)[0].children or []
        links = 0
        for pos, child in enumerate(children):
            if child.type != "link_open":
                continue
            links += 1
            href = child.attrGet("href") or ""
            try:
                url = canonical_url(href)
            except PipelineError:
                counts["rejected"] += 1
                continue
            label = []
            for part in children[pos + 1:]:
                if part.type == "link_close":
                    break
                if part.type in {"text", "code_inline", "image"}:
                    label.append(part.content)
            identity = hashlib.sha256(url.encode("utf-8")).hexdigest()
            if identity in found:
                counts["duplicates"] += 1
            else:
                found[identity] = Bookmark(identity, url, " ".join("".join(label).split()) or url)
        if not links:
            counts["rejected"] += 1
    return list(found.values()), counts
