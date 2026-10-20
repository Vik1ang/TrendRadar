from __future__ import annotations

import os
from typing import Any

import requests


class IntelligenceAPIError(RuntimeError):
    pass


class IngestionAPI:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 20):
        self.url = (url or os.environ.get("INTELLIGENCE_INGEST_URL", "")).strip()
        self.token = token or os.environ.get("INGEST_TOKEN", "")
        self.timeout = timeout
        self.session = requests.Session()
        if not self.url or not self.token:
            raise IntelligenceAPIError("INTELLIGENCE_INGEST_URL and INGEST_TOKEN are required")

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise IntelligenceAPIError("Ingestion API returned an invalid response")
            return result
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else "connection"
            raise IntelligenceAPIError(f"Ingestion API request failed ({status})") from exc
        except ValueError as exc:
            raise IntelligenceAPIError("Ingestion API returned invalid JSON") from exc

    def import_batch(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(self.url, body)

    def analysis_candidates(self, body: dict[str, Any]) -> list[str]:
        base = self.url.rsplit("/", 1)[0]
        result = self._post(f"{base}/analysis-candidates", body)
        article_ids = result.get("articleIds")
        if not isinstance(article_ids, list) or any(not isinstance(item, str) for item in article_ids):
            raise IntelligenceAPIError("Ingestion API returned invalid candidate IDs")
        return article_ids
