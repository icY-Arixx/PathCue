"""Bounded online discovery and safe page reading for travel evidence.

Search snippets discover pages; only text read from a source page can become a
visible citation. Applicability remains explicit because an official page can
still describe the wrong gate, direction, passenger group, or date.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path

from .text import split_sentences, tokenize

OFFICIAL_SUFFIXES = (".gov.cn", ".edu.cn", ".12306.cn", "12306.cn")


@dataclass(slots=True)
class KnowledgeNeed:
    subject: str
    issue: str
    city: str = ""
    local_date: str = ""
    local_time: str = ""
    direction: str = "unknown"
    audience: str = "passenger"
    affects_feasibility: bool = True


@dataclass(slots=True)
class SearchResult:
    url: str
    title: str
    description: str = ""


@dataclass(slots=True)
class Page:
    url: str
    title: str
    text: str
    retrieved_at: str


@dataclass(slots=True)
class EvidenceCandidate:
    url: str
    title: str
    quote: str
    retrieved_at: str
    source_kind: str
    applicability: str


@dataclass(slots=True)
class EvidenceResult:
    status: str
    need: KnowledgeNeed
    candidates: list[EvidenceCandidate]
    searched: bool
    message: str


def infer_knowledge_need(question: str, requirements: dict | None = None) -> KnowledgeNeed:
    requirements = requirements or {}
    direction = "egress" if any(word in question for word in ("出校", "出站", "离开", "从东门去", "从西门去")) else "unknown"
    if any(word in question for word in ("入校", "进校", "进站", "入馆")):
        direction = "ingress"
    places = [str(p) for p in (requirements.get("origin"), requirements.get("destination")) if p]
    question_tokens = set(tokenize(question))
    scored_places = []
    for place in places:
        place_tokens = set(tokenize(place))
        coverage = len(place_tokens & question_tokens) / max(1, len(place_tokens))
        scored_places.append((coverage, len(place), place))
    subject = str(requirements.get("origin") or "") if direction == "egress" else ""
    if direction == "ingress":
        subject = str(requirements.get("destination") or "")
    subject = subject or max(scored_places, default=(0, 0, ""))[2]
    if not subject:
        match = re.search(r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}(?:大学|校区|车站|火车站|机场|景区|博物馆|医院))", question)
        subject = match.group(1) if match else next((str(p) for p in places if p), "相关地点")
    audience = "visitor" if any(word in question for word in ("访客", "游客", "校外")) else "passenger"
    issue = "夜间开放与通行规则" if any(word in question for word in ("夜间", "晚上", "凌晨", "十二点", "24点")) else "入口、开放与通行规则"
    return KnowledgeNeed(
        subject=subject,
        issue=issue,
        city=str(requirements.get("city") or ""),
        local_date=str(requirements.get("date") or ""),
        local_time=str(requirements.get("depart_at") or requirements.get("arrive_by") or ""),
        direction=direction,
        audience=audience,
    )


class CloudflareWebSearch:
    def __init__(self, settings):
        self.account_id = settings.web_search_account_id
        self.api_token = settings.web_search_api_token
        self.gateway_id = settings.web_search_gateway_id
        self.provider = settings.web_search_provider
        self.byok_alias = settings.web_search_byok_alias
        self.timeout = settings.web_search_timeout_s
        self.limit = max(1, min(10, settings.web_search_max_results))

    @property
    def enabled(self) -> bool:
        return bool(self.account_id and self.api_token)

    def search(self, query: str) -> list[SearchResult]:
        if not self.enabled:
            raise RuntimeError("Web search is not configured")
        payload: dict = {
            "query": query[:1024],
            "provider": self.provider,
            "limit": self.limit,
            "options": {"gateway": {"id": self.gateway_id}},
        }
        if self.byok_alias:
            payload["byokAlias"] = self.byok_alias
        request = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/websearch/",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + self.api_token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read(256_000))
        # The standalone API currently returns items directly. Accept the
        # Cloudflare envelope as well so a gateway change fails closed, not open.
        items = body.get("items") or (body.get("result") or {}).get("items") or []
        results = []
        for item in items[: self.limit]:
            url = str(item.get("url") or "")
            if url:
                results.append(SearchResult(url=url, title=str(item.get("title") or url), description=str(item.get("description") or "")))
        return results


class TinyFishWebSearch:
    """TinyFish's free Search endpoint; snippets are discovery-only."""

    def __init__(self, settings):
        self.api_key = settings.tinyfish_api_key
        self.search_url = settings.tinyfish_search_url.rstrip("/")
        self.timeout = settings.web_search_timeout_s
        self.limit = max(1, min(10, settings.web_search_max_results))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str) -> list[SearchResult]:
        if not self.enabled:
            raise RuntimeError("TinyFish Search is not configured")
        params = urllib.parse.urlencode({"query": query[:1024]})
        request = urllib.request.Request(
            f"{self.search_url}?{params}",
            headers={"X-API-Key": self.api_key, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read(256_000))
        items = body.get("results") or []
        results = []
        for item in items[: self.limit]:
            url = str(item.get("url") or "")
            if url:
                results.append(
                    SearchResult(
                        url=url,
                        title=str(item.get("title") or url),
                        description=str(item.get("snippet") or ""),
                    )
                )
        return results


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []
        self.title: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "li", "br", "h1", "h2", "h3", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.hidden:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)
            if self.in_title:
                self.title.append(value)


class SafePageReader:
    def __init__(self, timeout_s: int = 8, max_bytes: int = 1_000_000):
        self.timeout_s = max(1, min(20, timeout_s))
        self.max_bytes = max(10_000, min(2_000_000, max_bytes))

    @staticmethod
    def validate_url(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Only public HTTPS pages are allowed")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise ValueError("Source host could not be resolved") from exc
        if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
            raise ValueError("Private or special network targets are blocked")
        return urllib.parse.urlunsplit(parsed._replace(fragment=""))

    def read(self, url: str) -> Page:
        safe_url = self.validate_url(url)
        reader = self

        class Redirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return super().redirect_request(req, fp, code, msg, headers, reader.validate_url(newurl))

        opener = urllib.request.build_opener(Redirects())
        request = urllib.request.Request(safe_url, headers={"User-Agent": "PathCueEvidenceBot/0.1 (+public travel evidence)"})
        with opener.open(request, timeout=self.timeout_s) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain"}:
                raise ValueError("Unsupported source type")
            raw = response.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise ValueError("Source page is too large")
            charset = response.headers.get_content_charset() or "utf-8"
        decoded = raw.decode(charset, errors="replace")
        if content_type == "text/plain":
            title, text = safe_url, decoded
        else:
            parser = _TextExtractor()
            parser.feed(decoded)
            title = " ".join(parser.title).strip() or safe_url
            text = " ".join(parser.parts)
        text = html.unescape(re.sub(r"[ \t]+", " ", re.sub(r"\n+", "\n", text))).strip()
        if len(text) < 80:
            raise ValueError("Source page has too little readable text")
        return Page(safe_url, title[:240], text, time.strftime("%Y-%m-%dT%H:%M:%S%z"))


class TinyFishFetchReader:
    """Read source text through TinyFish Fetch after validating the target URL."""

    def __init__(self, settings):
        self.api_key = settings.tinyfish_api_key
        self.fetch_url = settings.tinyfish_fetch_url.rstrip("/")
        self.timeout_s = max(1, min(20, settings.page_read_timeout_s))
        self.max_bytes = max(10_000, min(2_000_000, settings.page_read_max_bytes))

    def read(self, url: str) -> Page:
        if not self.api_key:
            raise RuntimeError("TinyFish Fetch is not configured")
        safe_url = SafePageReader.validate_url(url)
        request = urllib.request.Request(
            self.fetch_url,
            data=json.dumps({"urls": [safe_url]}).encode(),
            headers={
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            raw = response.read(self.max_bytes + 1)
        if len(raw) > self.max_bytes:
            raise ValueError("TinyFish Fetch response is too large")
        body = json.loads(raw)
        items = body.get("results") or []
        if not items:
            raise ValueError("TinyFish Fetch returned no readable source")
        item = items[0]
        text = str(item.get("text") or "").strip()
        if len(text) < 80:
            raise ValueError("Source page has too little readable text")
        return Page(
            url=str(item.get("url") or safe_url),
            title=str(item.get("title") or safe_url)[:240],
            text=text,
            retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )


class EvidenceCache:
    def __init__(self, path: Path, ttl_s: int = 86_400):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.ttl_s = path, max(60, ttl_s)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL)")

    def get(self, url: str) -> Page | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload, expires_at FROM pages WHERE url=?", (url,)).fetchone()
            if not row or row[1] <= time.time():
                return None
        return Page(**json.loads(row[0]))

    def put(self, page: Page) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO pages VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET payload=excluded.payload, expires_at=excluded.expires_at",
                (page.url, json.dumps(asdict(page), ensure_ascii=False), time.time() + self.ttl_s),
            )


def _official(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in OFFICIAL_SUFFIXES)


class EvidenceService:
    def __init__(self, search, reader: SafePageReader, cache: EvidenceCache, max_pages: int = 3):
        self.search, self.reader, self.cache = search, reader, cache
        self.max_pages = max(1, min(3, max_pages))

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.search, "enabled", True))

    def find(self, question: str, requirements: dict | None = None) -> EvidenceResult:
        need = infer_knowledge_need(question, requirements)
        query = " ".join(part for part in (need.city, need.subject, need.issue, question, "官方") if part)
        results = self.search.search(query)
        candidates: list[EvidenceCandidate] = []
        read_count = 0
        for result in sorted(results, key=lambda item: (not _official(item.url), item.url)):
            if read_count >= self.max_pages:
                break
            try:
                page = self.cache.get(result.url)
                if page is None:
                    page = self.reader.read(result.url)
                    self.cache.put(page)
                read_count += 1
            except (ValueError, OSError, urllib.error.URLError):
                continue
            query_tokens = set(tokenize(" ".join((need.subject, need.issue, question))))
            ranked = []
            for sentence in split_sentences(page.text):
                if not 20 <= len(sentence) <= 420:
                    continue
                sentence_tokens = set(tokenize(sentence))
                coverage = len(query_tokens & sentence_tokens) / max(1, len(query_tokens))
                ranked.append((coverage, sentence))
            if not ranked:
                continue
            score, quote = max(ranked, key=lambda item: item[0])
            if score <= 0:
                continue
            kind = "official" if _official(page.url) else "public_web"
            applicability = "candidate" if kind == "official" else "unverified_source"
            candidates.append(EvidenceCandidate(page.url, page.title or result.title, quote, page.retrieved_at, kind, applicability))
        if candidates:
            return EvidenceResult(
                "candidate_evidence",
                need,
                candidates,
                True,
                "已找到相关网页原文片段，但仍需核对地点、方向、人员与有效时间；暂不把它当作实时开放保证。",
            )
        return EvidenceResult("not_found", need, [], True, "联网检索未找到可读取且足以支持该陈述的来源，关键通行条件仍待确认。")


def build_evidence_service(settings) -> EvidenceService:
    backend = settings.online_search_backend
    if backend == "tinyfish":
        search = TinyFishWebSearch(settings)
        reader = TinyFishFetchReader(settings)
    elif backend == "cloudflare":
        search = CloudflareWebSearch(settings)
        reader = SafePageReader(settings.page_read_timeout_s, settings.page_read_max_bytes)
    else:
        raise ValueError(f"Unsupported ONLINE_SEARCH_BACKEND: {backend}")
    cache = EvidenceCache(settings.index_dir / "online_evidence.sqlite3", settings.evidence_cache_ttl_s)
    return EvidenceService(search, reader, cache)


def candidate_to_state(candidate: EvidenceCandidate, citation_id: int) -> dict:
    chunk_id = "web:" + hashlib.sha256((candidate.url + candidate.quote).encode()).hexdigest()[:20]
    return {
        "citation_id": citation_id,
        "doc_id": chunk_id,
        "claim": candidate.quote,
        "title": candidate.title,
        "source": candidate.url,
        "chunk_id": chunk_id,
        "metadata": {
            "source_url": candidate.url,
            "source_type": "web",
            "data_kind": "real",
            "retrieved_at": candidate.retrieved_at,
            "source_kind": candidate.source_kind,
            "applicability": candidate.applicability,
            "knowledge_scope": "online_candidate",
        },
        "scope_note": "联网候选证据；需核对地点、方向、人员和有效时间，不能代表实时开放状态。",
    }
