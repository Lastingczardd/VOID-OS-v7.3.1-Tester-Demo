"""Purpose-driven autonomous research with an auditable local evidence ledger.

The engine generates a bounded query from the current verified task, searches a
small allowlist of public knowledge APIs, stores raw source metadata, and asks
the local model to synthesize a concise research brief. Research failure never
stops the main autopilot loop.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class AutonomousResearchEngine:
    DEFAULT_DOMAINS = {
        "en.wikipedia.org",
        "export.arxiv.org",
        "api.crossref.org",
        "api.stackexchange.com",
    }

    def __init__(self, cfg: dict, router):
        self.cfg = cfg
        self.router = router
        self.settings = cfg.get("research", {})
        self.enabled = bool(self.settings.get("enabled", True))
        self.every_cycles = max(1, int(self.settings.get("every_cycles", 1)))
        self.timeout = max(3, int(self.settings.get("timeout_seconds", 18)))
        self.max_results = max(1, min(12, int(self.settings.get("max_results_per_source", 3))))
        self.max_bytes = max(16_384, min(2_000_000, int(self.settings.get("max_response_bytes", 500_000))))
        configured = self.settings.get("allowed_domains", [])
        self.allowed_domains = set(configured or self.DEFAULT_DOMAINS)
        self.user_agent = str(self.settings.get("user_agent", "VOID-OS-Research/7.1 (local autonomous engineering research)"))

    def should_run(self, cycle: int) -> bool:
        return self.enabled and cycle > 0 and cycle % self.every_cycles == 0

    def research_for_cycle(self, cycle: int, task: str, context: str, output_root: Path) -> dict[str, Any]:
        output_root = Path(output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        query = self._make_query(task, context)
        sources: list[dict[str, Any]] = []
        errors: list[str] = []
        for name, searcher in (
            ("Wikipedia", self._search_wikipedia),
            ("arXiv", self._search_arxiv),
            ("Crossref", self._search_crossref),
            ("Stack Exchange", self._search_stackexchange),
        ):
            try:
                sources.extend(searcher(query))
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        sources = self._deduplicate(sources)
        if not sources:
            record = {
                "status": "SKIPPED",
                "cycle": cycle,
                "query": query,
                "reason": "No approved research source returned usable evidence.",
                "errors": errors,
            }
            self._append_ledger(output_root, record)
            return record

        brief = self._synthesize(task, query, sources)
        slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:60] or "research"
        record_path = output_root / f"research_{cycle:04d}_{stamp}_{slug}.json"
        brief_path = output_root / f"research_{cycle:04d}_{stamp}_{slug}.md"
        payload = {
            "status": "COMPLETE",
            "cycle": cycle,
            "created_at": stamp,
            "task": task,
            "query": query,
            "sources": sources,
            "errors": errors,
            "brief_path": str(brief_path),
        }
        record_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        citations = "\n".join(
            f"{i}. [{s['title']}]({s['url']}) — {s['source']}" for i, s in enumerate(sources, 1)
        )
        brief_path.write_text(
            f"# Autonomous Research Brief\n\n**Query:** {query}\n\n"
            f"## Synthesis\n\n{brief}\n\n## Sources\n\n{citations}\n",
            encoding="utf-8",
        )
        payload.update({"record": str(record_path), "brief": str(brief_path), "digest": brief[:8000]})
        self._append_ledger(output_root, payload)
        return payload

    def _make_query(self, task: str, context: str) -> str:
        prompt = (
            "Create one precise public research search query for the software-engineering task below. "
            "Return only the query, no quotes, no explanation. Prefer technical concepts, failure names, "
            "standards, algorithms, or APIs. Do not include private paths, usernames, secrets, or project names.\n\n"
            f"TASK:\n{task[:3000]}\n\nEVIDENCE EXCERPT:\n{context[:5000]}"
        )
        try:
            query = self.router.generate(prompt, task="reasoning", temperature=0.15, max_tokens=80).strip()
            query = re.sub(r"[\r\n]+", " ", query)
            query = re.sub(r"\s+", " ", query).strip(" '\"")
        except Exception:
            query = task
        query = re.sub(r"(?:[A-Za-z]:\\|/)[^ ]+", "", query)
        return query[:240] or "software engineering reliability testing rollback architecture"

    def _request_json(self, url: str) -> Any:
        raw = self._request(url)
        return json.loads(raw.decode("utf-8", errors="replace"))

    def _request(self, url: str) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_domains:
            raise ValueError(f"Research URL is not allowlisted: {parsed.hostname}")
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json, application/atom+xml, text/xml"})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            data = response.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise RuntimeError("Research response exceeded configured size limit")
        return data

    def _search_wikipedia(self, query: str) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": query, "srlimit": self.max_results, "format": "json", "utf8": 1})
        data = self._request_json(f"https://en.wikipedia.org/w/api.php?{params}")
        out = []
        for item in data.get("query", {}).get("search", []):
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            out.append(self._source("Wikipedia", title, f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}", self._clean(item.get("snippet", ""))))
        return out

    def _search_arxiv(self, query: str) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"search_query": f"all:{query}", "start": 0, "max_results": self.max_results, "sortBy": "relevance"})
        raw = self._request(f"https://export.arxiv.org/api/query?{params}")
        root = ET.fromstring(raw)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for entry in root.findall("a:entry", ns):
            title = " ".join((entry.findtext("a:title", default="", namespaces=ns)).split())
            url = entry.findtext("a:id", default="", namespaces=ns)
            summary = " ".join((entry.findtext("a:summary", default="", namespaces=ns)).split())
            if title and url:
                out.append(self._source("arXiv", title, url, summary[:900]))
        return out

    def _search_crossref(self, query: str) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"query": query, "rows": self.max_results, "select": "DOI,title,URL,abstract,published"})
        data = self._request_json(f"https://api.crossref.org/works?{params}")
        out = []
        for item in data.get("message", {}).get("items", []):
            titles = item.get("title") or []
            title = str(titles[0] if titles else "").strip()
            url = str(item.get("URL") or "").strip()
            abstract = self._clean(item.get("abstract", ""))
            if title and url:
                out.append(self._source("Crossref", title, url, abstract[:900]))
        return out

    def _search_stackexchange(self, query: str) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"site": "stackoverflow", "intitle": query[:120], "pagesize": self.max_results, "order": "desc", "sort": "relevance", "filter": "default"})
        data = self._request_json(f"https://api.stackexchange.com/2.3/search?{params}")
        out = []
        for item in data.get("items", []):
            title = html.unescape(str(item.get("title", ""))).strip()
            url = str(item.get("link", "")).strip()
            if title and url:
                score = item.get("score", 0)
                answered = item.get("is_answered", False)
                out.append(self._source("Stack Overflow", title, url, f"Community question; score={score}; answered={answered}."))
        return out

    def _synthesize(self, task: str, query: str, sources: list[dict[str, Any]]) -> str:
        evidence = "\n\n".join(
            f"SOURCE {i}\nTitle: {s['title']}\nPublisher: {s['source']}\nURL: {s['url']}\nExcerpt: {s['excerpt']}"
            for i, s in enumerate(sources, 1)
        )
        prompt = (
            "Act as a cautious software research analyst. Using only the supplied source metadata and excerpts, "
            "write a compact engineering brief with: Findings, Relevance to Current Task, Candidate Approaches, "
            "Risks/Unknowns, and Verification Plan. Cite claims using [Source N]. Distinguish evidence from inference. "
            "Do not claim to have read content not included here.\n\n"
            f"CURRENT TASK:\n{task[:4000]}\n\nQUERY:\n{query}\n\nEVIDENCE:\n{evidence[:24000]}"
        )
        try:
            return self.router.generate(prompt, task="reasoning", temperature=0.25, max_tokens=1100, context_tokens=12288)
        except Exception as exc:
            return f"Research synthesis unavailable: {exc}\n\nCollected {len(sources)} source records for later review."

    @staticmethod
    def _source(source: str, title: str, url: str, excerpt: str) -> dict[str, Any]:
        return {"source": source, "title": title[:500], "url": url, "excerpt": excerpt[:1200], "id": hashlib.sha256(url.encode()).hexdigest()[:16]}

    @staticmethod
    def _clean(value: Any) -> str:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        return " ".join(html.unescape(text).split())

    @staticmethod
    def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        out = []
        for item in items:
            key = item.get("url") or item.get("title", "").lower()
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        return out

    @staticmethod
    def _append_ledger(root: Path, record: dict[str, Any]) -> None:
        ledger = root / "evidence_ledger.jsonl"
        compact = {
            "time": int(time.time()),
            "cycle": record.get("cycle"),
            "status": record.get("status"),
            "query": record.get("query"),
            "brief": record.get("brief"),
            "record": record.get("record"),
            "source_count": len(record.get("sources", [])),
            "errors": record.get("errors", []),
        }
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(compact, ensure_ascii=False) + "\n")
