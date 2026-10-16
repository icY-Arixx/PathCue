"""Small single-instance guard for public previews."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class PublicRequestGuard:
    protected_paths = {"/api/trips", "/api/location", "/query", "/feedback"}

    def __init__(self, enabled: bool, requests_per_minute: int):
        if requests_per_minute < 1:
            raise ValueError("PUBLIC_REQUESTS_PER_MINUTE must be positive")
        self.enabled = enabled
        self.limit = requests_per_minute
        self.requests = defaultdict(deque)
        self.lock = threading.Lock()

    @staticmethod
    def client_key(request) -> str:
        forwarded = request.headers.get("cf-connecting-ip")
        return forwarded or (request.client.host if request.client else "unknown")

    def allow(self, request) -> bool:
        if not self.enabled or request.method != "POST" or request.url.path not in self.protected_paths:
            return True
        now = time.monotonic()
        key = self.client_key(request)
        with self.lock:
            bucket = self.requests[key]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True
