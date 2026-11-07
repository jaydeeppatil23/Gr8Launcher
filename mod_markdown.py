"""Convert embedded HTML in Modrinth descriptions to native Markdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[str | _Element] = field(default_factory=list)


class _DescriptionParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "li", "tr", "td", "th"} and self.stack[-1].tag == tag:
            self.stack.pop()
        element = _Element(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(element)
        if tag not in self.VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _protect_code(value: str) -> tuple[str, dict[str, str]]:
    """Keep Markdown code examples out of the HTML parser."""
    prefix = "\ue000GR8_CODE_"
    while prefix in value:
        prefix += "_"
    protected: dict[str, str] = {}

    def save(source: str) -> str:
        token = f"{prefix}{len(protected)}\ue001"
        protected[token] = source
        return token

    lines = value.splitlines(keepends=True)
    output = []
    index = 0
    while index < len(lines):
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", lines[index])
        if fence:
            start = index
            index += 1
            closing = re.compile(r"^ {0,3}" + re.escape(fence[1][0]) + "{" + str(len(fence[1])) + r",}\s*$")
            while index < len(lines):
                line = lines[index]
                index += 1
                if closing.match(line):
                    break
            source = "".join(lines[start:index])
            output.append(save(source[:-1] if source.endswith("\n") else source) + ("\n" if source.endswith("\n") else ""))
        else:
            output.append(lines[index])
            index += 1
    value = "".join(output)
    value = re.sub(r"(?<!`)(`+)(?!`)(.*?)\1(?!`)", lambda match: save(match[0]), value, flags=re.DOTALL)
    value = re.sub(r"<(?:https?://|mailto:)[^<>\s]+>|<[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>",
                   lambda match: save(match[0]), value)
    # Inspect HTML context after protecting inline examples: a literal `<div>`
    # in inline code must not change how subsequent indented code is handled.
    context = _DescriptionParser()
    block_tags = {"pre", "div", "table", "ul", "ol", "details", "blockquote", "p", "section", "article", "figure",
                  "h1", "h2", "h3", "h4", "h5", "h6"}
    lines = value.splitlines(keepends=True)
    output = []
    index = 0
    while index < len(lines):
        if lines[index].startswith(("    ", "\t")) and not any(element.tag in block_tags for element in context.stack):
            start = index
            while index < len(lines) and (lines[index].startswith(("    ", "\t")) or not lines[index].strip()):
                index += 1
            source = "".join(lines[start:index])
            output.append(save(source[:-1] if source.endswith("\n") else source) + ("\n" if source.endswith("\n") else ""))
        else:
            output.append(lines[index])
            context.feed(lines[index])
            index += 1
    return "".join(output), protected


def _plain(element: str | _Element) -> str:
    if isinstance(element, str):
        return element
    if element.tag == "br":
        return "\n"
    return "".join(_plain(child) for child in element.children)


class _MarkdownRenderer:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def _url(self, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        resolved = value if value.startswith("#") else urljoin(self.base_url, value)
        if not resolved.startswith("#") and urlparse(resolved).scheme not in {"https", "http"}:
            return ""
        for char in " ()<>":
            resolved = resolved.replace(char, f"%{ord(char):02X}")
        return resolved

    def render(self, element: str | _Element) -> str:
        if isinstance(element, str):
            return element
        tag = element.tag
        if tag in {"script", "style", "svg", "noscript", "head"}:
            return ""
        if tag == "pre":
            source = _plain(element).strip("\n")
            code = next((child for child in element.children if isinstance(child, _Element) and child.tag == "code"), element)
            language = re.search(r"(?:language-|lang-)([\w+-]+)", code.attrs.get("class", ""))
            fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", source)), default=3))
            return f"\n\n{fence}{language[1] if language else ''}\n{source}\n{fence}\n\n"
        if tag == "code":
            source = _plain(element).replace("\n", " ")
            fence = "`" * max(1, max((len(run) + 1 for run in re.findall(r"`+", source)), default=1))
            padding = " " if source.startswith(("`", " ")) or source.endswith(("`", " ")) else ""
            return f"{fence}{padding}{source}{padding}{fence}"
        if tag == "table":
            return self._table(element)
        if tag in {"ul", "ol"}:
            return self._list(element)
        content = "".join(self.render(child) for child in element.children)
        if tag == "a":
            url = self._url(element.attrs.get("href", ""))
            return f"[{content.strip() or url}]({url})" if url else content
        if tag == "img":
            url = self._url(element.attrs.get("src", ""))
            alt = element.attrs.get("alt", "").replace("[", r"\[").replace("]", r"\]")
            return f"![{alt}]({url})" if url else alt
        if tag in {"iframe", "video", "audio"}:
            url = self._url(element.attrs.get("src", ""))
            return f"\n\n[View media]({url})\n\n" if url else content
        if tag in {"strong", "b", "em", "i", "s", "del", "strike"}:
            marker = "**" if tag in {"strong", "b"} else "*" if tag in {"em", "i"} else "~~"
            return marker + content.strip() + marker if content.strip() else ""
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return f"\n\n{'#' * int(tag[1])} {content.strip()}\n\n"
        if tag == "summary":
            return f"\n\n### {content.strip()}\n\n"
        if tag == "blockquote":
            return "\n\n" + "\n".join("> " + line for line in content.strip().splitlines()) + "\n\n"
        if tag == "br":
            return "  \n"
        if tag == "hr":
            return "\n\n---\n\n"
        if tag in {"p", "div", "section", "article", "center", "details", "figure", "figcaption", "dl", "dt", "dd"}:
            return "\n\n" + content.strip() + "\n\n" if content.strip() else ""
        return content

    def _list(self, element: _Element) -> str:
        rows = []
        try:
            start = int(element.attrs.get("start", "1"))
        except ValueError:
            start = 1
        for child in element.children:
            if not isinstance(child, _Element) or child.tag != "li":
                if isinstance(child, _Element) or child.strip():
                    rows.append(self.render(child).strip())
                continue
            content = self.render(child).strip().splitlines()
            marker = f"{start}. " if element.tag == "ol" else "- "
            rows.append(marker + (content[0] if content else ""))
            rows.extend(" " * len(marker) + line if line else "" for line in content[1:])
            start += 1
        return "\n\n" + "\n".join(rows) + "\n\n"

    def _table(self, element: _Element) -> str:
        def rows(node: _Element):
            if node.tag == "tr":
                yield node
            else:
                for child in node.children:
                    if isinstance(child, _Element):
                        yield from rows(child)
        data = []
        for row in rows(element):
            cells = [re.sub(r"\s*\n\s*", " / ", self.render(cell).strip()).replace("|", r"\|")
                     for cell in row.children if isinstance(cell, _Element) and cell.tag in {"td", "th"}]
            if cells:
                data.append(cells)
        if not data:
            return "".join(self.render(child) for child in element.children)
        width = max(map(len, data))
        data = [row + [""] * (width - len(row)) for row in data]
        data.insert(1, ["---"] * width)
        return "\n\n" + "\n".join("| " + " | ".join(row) + " |" for row in data) + "\n\n"


@lru_cache(maxsize=64)
def normalize_mod_markdown(value: str, base_url: str = "https://modrinth.com/") -> str:
    """Preserve native Markdown and convert embedded HTML without running it."""
    source, protected = _protect_code(value)
    if re.search(r"</?[A-Za-z][^>]*>|<!--", source):
        parser = _DescriptionParser()
        parser.feed(source)
        parser.close()
        source = _MarkdownRenderer(base_url).render(parser.root)
        source = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", source).strip()
    for token, original in reversed(list(protected.items())):
        source = source.replace(token, original)
    return source
