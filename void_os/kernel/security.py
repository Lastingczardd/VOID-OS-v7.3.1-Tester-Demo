"""Security helpers for local-first VOID OS Forge."""
from __future__ import annotations
import hashlib, hmac, json, os, re, secrets
from pathlib import Path
from urllib.parse import urlparse
from .paths import BASE, DATA, PROJECTS, PLUGINS

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$")
_SECRET_KEYS = re.compile(r"(token|secret|password|authorization|api[_-]?key|cookie)", re.I)


def safe_segment(value: str, label: str = "name") -> str:
    value = str(value or "").strip()
    if not _SAFE_SEGMENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"Invalid {label}.")
    return value


def confined_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    if candidate != root and root not in candidate.parents:
        raise PermissionError("Path escapes its allowed directory.")
    return candidate


def enforce_local_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP(S) model endpoints are allowed.")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise PermissionError("Remote model endpoints are blocked in local-only mode.")


def api_token_path() -> Path:
    return DATA / "secrets" / "api_token.txt"


def get_or_create_api_token() -> str:
    path = api_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def constant_time_token_ok(provided: str | None, expected: str) -> bool:
    return bool(provided) and hmac.compare_digest(provided, expected)


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v)) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    if isinstance(value, tuple): return tuple(redact(v) for v in value)
    text = str(value)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/]+=*", r"\1[REDACTED]", text)
    return text[:4000]


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def verify_core_manifest() -> tuple[bool,str]:
    manifest_path=BASE/'CORE_MANIFEST_SHA256.json'
    if not manifest_path.exists(): return False, 'Core integrity manifest is missing.'
    try: manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception as e: return False, f'Core manifest is invalid: {e}'
    for rel, expected in manifest.items():
        path=confined_path(BASE, rel)
        if not path.is_file(): return False, f'Missing core file: {rel}'
        if sha256_file(path) != expected: return False, f'Core file changed: {rel}'
    return True, 'Core integrity verified.'
