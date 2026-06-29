import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Document:
    source: str
    title: str
    text: str


def _strip_markdown(text: str) -> str:
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'[*_]{1,3}', '', text)
    text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
    return text.strip()


def _extract_docstrings(text: str) -> str:
    docstrings = re.findall(r'"""(.*?)"""', text, re.DOTALL)
    return '\n'.join(d.strip() for d in docstrings)


def load_documents(project_root: str | None = None) -> list[Document]:
    """Сканирует проект, собирает .md и .py файлы, извлекает текст."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    docs: list[Document] = []

    lessions_dir = os.path.join(project_root, 'docs', 'lessions')
    if os.path.isdir(lessions_dir):
        for fname in sorted(os.listdir(lessions_dir)):
            if fname.endswith('.md'):
                path = os.path.join(lessions_dir, fname)
                text = _strip_markdown(Path(path).read_text(encoding='utf-8'))
                if text:
                    docs.append(Document(
                        source=os.path.relpath(path, project_root),
                        title=fname.replace('.md', ''),
                        text=text,
                    ))

    for week_dir in sorted(os.listdir(project_root)):
        if not re.match(r'^week-\d{2}$', week_dir):
            continue
        week_path = os.path.join(project_root, week_dir)
        if not os.path.isdir(week_path):
            continue
        for day_dir in sorted(os.listdir(week_path)):
            readme = os.path.join(week_path, day_dir, 'README.md')
            if os.path.isfile(readme):
                text = _strip_markdown(Path(readme).read_text(encoding='utf-8'))
                if text:
                    docs.append(Document(
                        source=os.path.relpath(readme, project_root),
                        title=f'{week_dir}/{day_dir}',
                        text=text,
                    ))

    agents_md = os.path.join(project_root, 'AGENTS.md')
    if os.path.isfile(agents_md):
        text = _strip_markdown(Path(agents_md).read_text(encoding='utf-8'))
        if text:
            docs.append(Document(
                source='AGENTS.md',
                title='AGENTS.md',
                text=text,
            ))

    agents_dir = os.path.join(project_root, 'agents')
    if os.path.isdir(agents_dir):
        for fname in sorted(os.listdir(agents_dir)):
            if fname.endswith('.py'):
                path = os.path.join(agents_dir, fname)
                text = _extract_docstrings(Path(path).read_text(encoding='utf-8'))
                if text:
                    docs.append(Document(
                        source=os.path.relpath(path, project_root),
                        title=fname.replace('.py', ''),
                        text=text,
                    ))

    return docs
