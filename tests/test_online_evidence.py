import json
import socket
from dataclasses import replace
from io import BytesIO
from unittest.mock import patch

import pytest

from tests.test_trips import ROOT
from urban_mobility_rag.config import Settings
from urban_mobility_rag.online_evidence import (
    CloudflareWebSearch,
    EvidenceCache,
    EvidenceService,
    Page,
    SafePageReader,
    SearchResult,
    TinyFishFetchReader,
    TinyFishWebSearch,
    build_evidence_service,
    candidate_to_state,
    infer_knowledge_need,
)


class Search:
    enabled = True

    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self.results


class Reader:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.urls = []

    def read(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.pages[url]


def test_knowledge_need_keeps_direction_time_and_subject():
    need = infer_knowledge_need(
        "北京大学晚上十二点从东门出校去北京站，东门开吗？",
        {"origin": "北京大学东门", "destination": "北京站", "city": "北京市", "date": "2026-10-17", "depart_at": "00:00"},
    )
    assert need.subject == "北京大学东门"
    assert need.direction == "egress" and need.city == "北京市"
    assert need.local_time == "00:00" and "夜间" in need.issue


def test_cloudflare_search_uses_documented_contract_without_leaking_token(tmp_path):
    settings = Settings(
        ROOT,
        ROOT / "data/knowledge_base",
        tmp_path,
        tmp_path / "feedback.jsonl",
        web_search_account_id="account-1",
        web_search_api_token="private-token",
        web_search_gateway_id="travel",
        web_search_provider="ceramic",
        web_search_max_results=3,
    )
    response = BytesIO(json.dumps({"items": [{"url": "https://example.edu.cn/gate", "title": "校门通知", "description": "摘要"}]}).encode())
    with patch("urban_mobility_rag.online_evidence.urllib.request.urlopen", return_value=response) as http:
        results = CloudflareWebSearch(settings).search("大学东门 官方")
    request = http.call_args.args[0]
    body = json.loads(request.data)
    assert body == {"query": "大学东门 官方", "provider": "ceramic", "limit": 3, "options": {"gateway": {"id": "travel"}}}
    assert request.headers["Authorization"] == "Bearer private-token"
    assert "private-token" not in request.data.decode() and results[0].description == "摘要"


def test_tinyfish_search_uses_key_header_and_keeps_snippet_discovery_only(tmp_path):
    settings = Settings(
        ROOT,
        ROOT / "data/knowledge_base",
        tmp_path,
        tmp_path / "feedback.jsonl",
        tinyfish_api_key="tiny-private-key",
        web_search_max_results=3,
    )
    response = BytesIO(
        json.dumps(
            {
                "results": [
                    {
                        "url": "https://example.edu.cn/notice",
                        "title": "临时交通公告",
                        "snippet": "搜索摘要只用于发现页面",
                    }
                ]
            }
        ).encode()
    )
    with patch("urban_mobility_rag.online_evidence.urllib.request.urlopen", return_value=response) as http:
        results = TinyFishWebSearch(settings).search("武汉 临时交通管制")
    request = http.call_args.args[0]
    assert request.full_url == "https://api.search.tinyfish.ai?query=%E6%AD%A6%E6%B1%89+%E4%B8%B4%E6%97%B6%E4%BA%A4%E9%80%9A%E7%AE%A1%E5%88%B6"
    assert request.get_header("X-api-key") == "tiny-private-key"
    assert "tiny-private-key" not in request.full_url
    assert results[0].description == "搜索摘要只用于发现页面"


def test_tinyfish_fetch_reads_page_text_without_putting_key_in_body(tmp_path):
    settings = Settings(
        ROOT,
        ROOT / "data/knowledge_base",
        tmp_path,
        tmp_path / "feedback.jsonl",
        tinyfish_api_key="tiny-private-key",
    )
    text = "武汉市有关部门发布临时交通管理公告，具体道路、方向和有效时间以公告正文为准。" * 3
    response = BytesIO(
        json.dumps(
            {"results": [{"url": "https://www.wuhan.gov.cn/notice", "title": "官方公告", "text": text}], "errors": []}
        ).encode()
    )
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with (
        patch("urban_mobility_rag.online_evidence.socket.getaddrinfo", return_value=public),
        patch("urban_mobility_rag.online_evidence.urllib.request.urlopen", return_value=response) as http,
    ):
        page = TinyFishFetchReader(settings).read("https://www.wuhan.gov.cn/notice")
    request = http.call_args.args[0]
    assert json.loads(request.data) == {"urls": ["https://www.wuhan.gov.cn/notice"]}
    assert request.get_header("X-api-key") == "tiny-private-key"
    assert "tiny-private-key" not in request.data.decode()
    assert page.title == "官方公告" and page.text == text


def test_tinyfish_is_default_online_backend_and_requires_only_its_key(tmp_path):
    base = Settings(ROOT, ROOT / "data/knowledge_base", tmp_path, tmp_path / "feedback.jsonl")
    assert build_evidence_service(base).enabled is False
    configured = replace(base, tinyfish_api_key="key")
    service = build_evidence_service(configured)
    assert service.enabled is True
    assert isinstance(service.search, TinyFishWebSearch)
    assert isinstance(service.reader, TinyFishFetchReader)


def test_page_reader_blocks_non_https_and_private_targets():
    with pytest.raises(ValueError):
        SafePageReader.validate_url("http://example.com/page")
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    with patch("urban_mobility_rag.online_evidence.socket.getaddrinfo", return_value=private):
        with pytest.raises(ValueError, match="Private"):
            SafePageReader.validate_url("https://internal.example/page")


def test_search_snippet_is_never_used_as_citation_and_official_page_is_prioritized(tmp_path):
    official = "https://www.example.edu.cn/notice"
    blog = "https://travel.example.com/post"
    search = Search(
        [
            SearchResult(blog, "游记", "博客声称东门全天开放"),
            SearchResult(official, "校方通知", "搜索摘要不是证据"),
        ]
    )
    reader = Reader(
        {
            official: Page(
                official,
                "校方通行通知",
                "北京大学东门夜间通行安排如下：访客入校需要预约，具体开放时段以校门现场公告为准。",
                "2026-10-16T20:00:00+08:00",
            ),
            blog: Page(blog, "游记", "这是个人旅行记录，没有官方适用说明。", "2026-10-16T20:00:00+08:00"),
        }
    )
    service = EvidenceService(search, reader, EvidenceCache(tmp_path / "cache.sqlite3"), max_pages=1)
    result = service.find("北京大学东门晚上十二点可以出校吗？", {"city": "北京市", "origin": "北京大学东门"})
    assert result.status == "candidate_evidence" and len(result.candidates) == 1
    candidate = result.candidates[0]
    assert reader.urls == [official] and candidate.source_kind == "official"
    assert candidate.quote in reader.pages[official].text
    assert "搜索摘要不是证据" not in candidate.quote and "全天开放" not in candidate.quote
    state = candidate_to_state(candidate, 1)
    assert state["claim"] == candidate.quote and state["metadata"]["applicability"] == "candidate"


def test_unreadable_pages_return_explicit_not_found(tmp_path):
    search = Search([SearchResult("https://example.edu.cn/notice", "通知", "不能引用的摘要")])
    service = EvidenceService(search, Reader(error=ValueError("blocked")), EvidenceCache(tmp_path / "cache.sqlite3"))
    result = service.find("校门晚上开吗", {"city": "北京市"})
    assert result.status == "not_found" and result.candidates == []
    assert "仍待确认" in result.message


def test_online_search_is_disabled_until_both_cloudflare_credentials_exist(tmp_path):
    base = Settings(ROOT, ROOT / "data/knowledge_base", tmp_path, tmp_path / "feedback.jsonl")
    assert CloudflareWebSearch(base).enabled is False
    assert CloudflareWebSearch(replace(base, web_search_account_id="only-account")).enabled is False
    assert CloudflareWebSearch(replace(base, web_search_account_id="a", web_search_api_token="t")).enabled is True
