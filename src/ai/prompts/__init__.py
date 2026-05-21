from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def _load(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


ANALYZER_SYSTEM_PROMPT = _load("analyzer")
RESPONDER_SYSTEM_PROMPT = _load("responder")
PROFILER_SYSTEM_PROMPT = _load("profiler")

__all__ = [
    "ANALYZER_SYSTEM_PROMPT",
    "PROFILER_SYSTEM_PROMPT",
    "RESPONDER_SYSTEM_PROMPT",
]
