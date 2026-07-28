"""Autonomous local image generation through an AUTOMATIC1111-compatible API."""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..kernel.security import enforce_local_url
from ..kernel.witness import log


class AutonomousImageGenerator:
    """Generate project visuals without adding operator planning controls."""

    def __init__(self, cfg: dict, router):
        self.cfg = cfg
        self.router = router
        self.settings = cfg.get("image_generation", {})
        self.base_url = str(self.settings.get("url", "http://127.0.0.1:7860")).rstrip("/")

    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    def should_run(self, cycle: int) -> bool:
        every = max(1, int(self.settings.get("every_cycles", 2)))
        return self.enabled() and cycle % every == 0

    def is_available(self) -> bool:
        if not self.enabled():
            return False
        try:
            enforce_local_url(self.base_url)
            with urllib.request.urlopen(self.base_url + "/sdapi/v1/sd-models", timeout=3) as response:
                return response.status == 200
        except Exception:
            return False

    def generate_for_cycle(self, cycle: int, task: str, evidence: str, result: str,
                           output_dir: Path) -> dict:
        if not self.enabled():
            return {"status": "DISABLED"}
        if not self.is_available():
            return {
                "status": "SKIPPED",
                "reason": "No local AUTOMATIC1111-compatible image server was found at " + self.base_url,
            }

        prompt = self._design_prompt(task, evidence, result)
        payload = {
            "prompt": prompt,
            "negative_prompt": str(self.settings.get(
                "negative_prompt",
                "blurry, low quality, unreadable text, watermark, duplicate objects, distorted anatomy"
            )),
            "steps": int(self.settings.get("steps", 24)),
            "width": int(self.settings.get("width", 768)),
            "height": int(self.settings.get("height", 768)),
            "cfg_scale": float(self.settings.get("cfg_scale", 7.0)),
            "sampler_name": str(self.settings.get("sampler_name", "DPM++ 2M Karras")),
            "batch_size": 1,
            "n_iter": 1,
        }
        enforce_local_url(self.base_url)
        request = urllib.request.Request(
            self.base_url + "/sdapi/v1/txt2img",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        timeout = int(self.settings.get("timeout_seconds", 600))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(64 * 1024 * 1024 + 1)
            if len(raw) > 64 * 1024 * 1024:
                raise RuntimeError("Image response exceeded 64 MiB safety limit.")
            body = json.loads(raw.decode("utf-8"))
            images = body.get("images") or []
            if not images:
                raise RuntimeError("The image server returned no images.")
            encoded = str(images[0]).split(",", 1)[-1]
            image_bytes = base64.b64decode(encoded, validate=True)
            if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("The image server did not return a valid PNG image.")
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            image_path = output_dir / f"autopilot_visual_{cycle:04d}_{stamp}.png"
            prompt_path = output_dir / f"autopilot_visual_{cycle:04d}_{stamp}.json"
            image_path.write_bytes(image_bytes)
            prompt_path.write_text(json.dumps({
                "cycle": cycle,
                "task": task,
                "prompt": prompt,
                "settings": payload,
                "image": str(image_path),
            }, indent=2), encoding="utf-8")
            record = {"status": "GENERATED", "image": str(image_path), "prompt": prompt}
            log("autonomous_image_generated", record)
            return record
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError, RuntimeError) as exc:
            record = {"status": "FAILED", "error": str(exc)}
            log("autonomous_image_generation_failed", record)
            return record

    def _design_prompt(self, task: str, evidence: str, result: str) -> str:
        instruction = (
            "Create one production-ready image-generation prompt for a visual that supports the current "
            "software project cycle. Choose the most useful visual form yourself, such as concept art, UI mockup, "
            "architecture illustration, feature card, icon sheet, branded poster, or explanatory diagram. "
            "Ground it in the task and result. Avoid copyrighted characters, logos, signatures, and long rendered text. "
            "Return only the final image prompt in one paragraph, under 900 characters.\n\n"
            f"TASK:\n{task}\n\nPROJECT EVIDENCE:\n{evidence[:3500]}\n\nRESULT:\n{result[:5000]}"
        )
        try:
            prompt = self.router.generate(instruction, task="reasoning", temperature=0.8, max_tokens=280)
        except Exception:
            prompt = (
                "Cinematic technical concept art representing the completed software improvement, clean modular "
                "architecture, autonomous agents coordinating around a luminous central workspace, precise interfaces, "
                "high detail, professional product visualization, dark background, emerald and gold accents, no text"
            )
        return " ".join(str(prompt).strip().split())[:900]
