"""Thin client for a local Ollama instance."""

import json
import time
import urllib.error
import urllib.request
from ..kernel.security import enforce_local_url


class ModelRouter:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def choose(self, task: str) -> str:
        """Pick a model name for a task, falling back to the chat model."""
        models = self.cfg.get("models", {})
        return models.get(task) or models.get("chat", "qwen2.5:3b")

    def is_available(self) -> bool:
        """Quick liveness check against Ollama, used by the UI status bar."""
        base = self.cfg["ollama_url"].split("/api/")[0]
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=3):
                return True
        except Exception:
            return False

    def generate(self, prompt: str, task: str = "chat", temperature: float = None,
                 max_tokens: int = None, context_tokens: int = None) -> str:
        """Call Ollama's /api/generate with retries. Raises RuntimeError with
        a clear message if every attempt fails."""
        prompt = str(prompt or "")
        max_chars = int(self.cfg.get("security", {}).get("max_prompt_chars", 50000))
        if len(prompt) > max_chars:
            raise ValueError(f"Prompt exceeds security limit of {max_chars} characters.")
        if self.cfg.get("security", {}).get("local_only", True):
            enforce_local_url(self.cfg["ollama_url"])
        model = self.choose(task)
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature if temperature is not None else self.cfg.get("temperature", 0.65),
                "num_predict": max_tokens if max_tokens is not None else self.cfg.get("max_output_tokens", 600),
                "num_ctx": context_tokens if context_tokens is not None else self.cfg.get("model_context_tokens", 8192),
            },
        }
        req = urllib.request.Request(
            self.cfg["ollama_url"],
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        timeout = self.cfg.get("request_timeout_seconds", 240)
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read(16 * 1024 * 1024 + 1)
                    if len(raw) > 16 * 1024 * 1024:
                        raise RuntimeError("Model response exceeded 16 MiB limit.")
                    body = json.loads(raw.decode())
                    return body.get("response", "").strip()
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.reason}"
            except urllib.error.URLError as e:
                last_error = f"Could not reach Ollama at {self.cfg['ollama_url']} ({e.reason})"
            except Exception as e:
                last_error = str(e)
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(
            f"Ollama request for model '{model}' failed after 3 attempts: {last_error}"
        )
