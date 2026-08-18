from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import re

MAX_ARTICLE_CHARS = 1_500_000
MAX_SECTION_CHARS = 12_000
MAX_SECTIONS = 300
TARGET_MIN_WORDS = 300
TARGET_MAX_WORDS = 800


@dataclass(frozen=True)
class ExtractedSection:
    heading: str
    text: str


class _ArticleParser(HTMLParser):
    SKIP = {"script", "style", "nav", "noscript", "form", "button", "svg", "table"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0; self.current_tag = ""; self.buffer: list[str] = []; self.blocks: list[tuple[str,str]] = []
    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").casefold()
        citation = tag == "sup" and ("reference" in classes or "mw-ref" in classes)
        if self.skip_depth or tag in self.SKIP or citation or attributes.get("hidden") is not None or "mw-editsection" in classes or "navbox" in classes:
            self.skip_depth += 1; return
        if tag in self.BLOCK:
            self._flush(); self.current_tag = tag
    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1; return
        if tag in self.BLOCK: self._flush()
    def handle_data(self, data: str) -> None:
        if not self.skip_depth and self.current_tag: self.buffer.append(data)
    def _flush(self) -> None:
        text = " ".join("".join(self.buffer).split())
        if text: self.blocks.append((self.current_tag, text))
        self.buffer=[]; self.current_tag=""


def extract_sections(html: str, *, max_article_chars: int = MAX_ARTICLE_CHARS, max_section_chars: int = MAX_SECTION_CHARS, max_sections: int = MAX_SECTIONS) -> tuple[list[ExtractedSection], bool]:
    parser = _ArticleParser(); parser.feed(html); parser.close(); parser._flush()
    heading = "Introduction"; grouped: list[tuple[str,list[str]]] = [(heading, [])]
    total = 0; truncated = False
    for tag, text in parser.blocks:
        if tag.startswith("h"):
            heading = re.sub(r"\[edit\]\s*$", "", text).strip() or "Introduction"
            grouped.append((heading, [])); continue
        if re.fullmatch(r"\[\d+\]", text): continue
        remaining = max_article_chars-total
        if remaining <= 0: truncated=True; break
        if len(text)>remaining: text=text[:remaining].rsplit(" ",1)[0]; truncated=True
        grouped[-1][1].append(text); total += len(text)
    result: list[ExtractedSection] = []
    for heading, paragraphs in grouped:
        if not paragraphs: continue
        result.extend(_chunk_paragraphs(heading, paragraphs, max_section_chars))
        if len(result)>=max_sections: truncated=True; return result[:max_sections], truncated
    return result, truncated


def _chunk_paragraphs(heading: str, paragraphs: list[str], max_chars: int) -> list[ExtractedSection]:
    chunks: list[str]=[]; current=""
    for paragraph in paragraphs:
        pieces = _split_sentences(paragraph, max_chars)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip()
            if current and (len(candidate)>max_chars or len(candidate.split())>TARGET_MAX_WORDS):
                chunks.append(current); current=piece
            else: current=candidate
    if current: chunks.append(current)
    # Avoid a tiny tail by merging when bounded.
    if len(chunks)>1 and len(chunks[-1].split())<80 and len(chunks[-2])+len(chunks[-1])+2<=max_chars:
        chunks[-2] += "\n\n"+chunks.pop()
    return [ExtractedSection(heading, chunk) for chunk in chunks if len(chunk.split())>=3]


def _split_sentences(text: str, max_chars: int) -> list[str]:
    if len(text)<=max_chars: return [text]
    sentences=re.split(r"(?<=[.!?])\s+", text); output=[]; current=""
    for sentence in sentences:
        while len(sentence)>max_chars:
            output.append(sentence[:max_chars].rsplit(" ",1)[0]); sentence=sentence[len(output[-1]):].strip()
        candidate=f"{current} {sentence}".strip()
        if current and len(candidate)>max_chars: output.append(current); current=sentence
        else: current=candidate
    if current: output.append(current)
    return output


def content_hash(sections: list[ExtractedSection]) -> str:
    canonical="\n".join(f"{s.heading}\n{s.text}" for s in sections)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approximate_tokens(text: str) -> int:
    return max(1, (len(text)+3)//4)
