# AI Advent Challenge — Agent guide

## Project nature

Educational challenge series — not an app/library. Each `w{week}t{task}.py` is a self-contained
script. No build, test, lint, typecheck, or CI tooling exists.

## Run any script

```bash
pip install -r requirements.txt   # python-dotenv (single dep)
python w01t03.py                   # or any w{week}t{task}.py
```

Requires `.env` with `CLOUDRU_SECRET_KEY` (see `.env.example`).  
API: OpenAI-compatible `POST /chat/completions` against `foundation-models.api.cloud.ru/v1`.

## Architecture

- **Agent core** → `agents/jarvis.py` (`JarvisAgent` class)
  - Pure stdlib: `urllib.request`, `json`, `sqlite3`
  - No `__init__.py` in `agents/` (namespace package, Python 3.3+)
  - DB auto-created at `memory/jarvis_history.db`
- **Entrypoints** → `w01t01.py`–`w05t01.py`, `w02t02.py`

## Conventions

- Russian throughout: README, comments, prompts, results
- Commits are Russian (`git log`): `add: Задание N (неделя N)`
- No `.venv` at root; `.venv-win/` is Windows-only and unusable from WSL/Linux
