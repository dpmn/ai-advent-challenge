import re
from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_id: str
    source: str
    title: str
    section: str
    text: str
    strategy: str
    token_count: int


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_fixed_size(
    docs: list,
    chunk_size: int = 1000,
    overlap: int = 100,
) -> list[Chunk]:
    """Фиксированный размер чанка + overlap."""
    chunks: list[Chunk] = []
    idx = 0
    char_size = chunk_size * 4
    char_overlap = overlap * 4

    for doc in docs:
        text = doc.text
        start = 0
        while start < len(text):
            end = min(start + char_size, len(text))
            fragment = text[start:end].strip()
            if not fragment:
                break
            chunks.append(Chunk(
                chunk_id=f'fixed_{idx:05d}',
                source=doc.source,
                title=doc.title,
                section=f'pos_{start}-{end}',
                text=fragment,
                strategy='fixed',
                token_count=_approx_tokens(fragment),
            ))
            idx += 1
            if end == len(text):
                break
            start = end - char_overlap

    return chunks


def chunk_structural(
    docs: list,
    max_tokens: int = 2000,
) -> list[Chunk]:
    """Чанкинг по заголовкам Markdown. Без заголовков — весь документ целиком."""
    chunks: list[Chunk] = []
    idx = 0

    for doc in docs:
        text = doc.text
        lines = text.split('\n')

        sections: list[tuple[str, str]] = []
        current_heading = doc.title
        current_lines: list[str] = []

        for line in lines:
            m = re.match(r'^(#{1,3})\s+(.+)$', line)
            if m and current_lines:
                section_text = '\n'.join(current_lines).strip()
                if section_text:
                    sections.append((current_heading, section_text))
                current_heading = m.group(2).strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            section_text = '\n'.join(current_lines).strip()
            if section_text:
                sections.append((current_heading, section_text))

        if not sections:
            sections = [(doc.title, text)]

        for heading, section_text in sections:
            if _approx_tokens(section_text) > max_tokens:
                char_limit = max_tokens * 4
                for i in range(0, len(section_text), char_limit):
                    sub = section_text[i:i + char_limit].strip()
                    if not sub:
                        continue
                    chunks.append(Chunk(
                        chunk_id=f'struct_{idx:05d}',
                        source=doc.source,
                        title=doc.title,
                        section=heading,
                        text=sub,
                        strategy='structural',
                        token_count=_approx_tokens(sub),
                    ))
                    idx += 1
            else:
                section_text = section_text.strip()
                if section_text:
                    chunks.append(Chunk(
                        chunk_id=f'struct_{idx:05d}',
                        source=doc.source,
                        title=doc.title,
                        section=heading,
                        text=section_text,
                        strategy='structural',
                        token_count=_approx_tokens(section_text),
                    ))
                    idx += 1

    return chunks
