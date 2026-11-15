import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Iterator
from urllib.parse import urlencode

import scrapy
from scrapy.http import Request, Response


class HackernewsScaperSpider(scrapy.Spider):
    name = "hackernews_scaper"
    allowed_domains = ["hn.algolia.com"]
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
    }
    custom_settings = {
        "AUTOTHROTTLE_ENABLED": True,
        "CONCURRENT_REQUESTS": 16,
        "DOWNLOAD_DELAY": 0.25,
    }

    ROOT_URL = "https://hn.algolia.com/api/v1/search_by_date"

    # has to be in ISO 8601
    START_DATE = "2025-01-01"
    END_DATE = "2026-11-01"

    HIT_PER_PAGES = 1000  # algolia caps 1000 response per API request

    WINDOW_SECONDS = 24 * 3600  # seconds in one day

    def _build_request_url(
        self, start_timestamp: int, end_timestamp: int, current_page: int = 0
    ) -> str:
        """Build Request API for HackerNews Algolia"""
        url_params = {
            "tags": "story",
            "numericFilters": f"created_at_i>={start_timestamp},created_at_i<{end_timestamp}",
            "hitsPerPage": self.HIT_PER_PAGES,
            "page": current_page,
        }
        return f"{self.ROOT_URL}?{urlencode(url_params)}"

    def _window_request(self, start_timestamp: int, end_timestamp: int) -> Request:
        """Return a reusable HTTP Request that send API call to HN Algolia endpoint with flexible timestamp"""
        return Request(
            self._build_request_url(start_timestamp, end_timestamp),
            callback=self.parse,
            headers=self.headers,
            cb_kwargs={
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
        )

    async def start(self) -> AsyncIterator[Request]:
        """Scrapy's standard Async Start function"""
        start_timestamp, end_timestamp = self._iso_dates_to_unix_timestamps()
        for window_start in range(start_timestamp, end_timestamp, self.WINDOW_SECONDS):
            window_end = min(window_start + self.WINDOW_SECONDS, end_timestamp)
            yield self._window_request(window_start, window_end)

    def parse(
        self, response: Response, start_timestamp: int, end_timestamp: int
    ) -> Iterator:
        """scrapy's standard parse function that:
        - Check API response
        - Split API response into multiple small timestamp to bypass Algolia limit
        -Yield the scraped items"""
        try:
            json_api_response = response.json()
        except Exception as e:
            logging.error(f"Come across {e} while requesting data from the API")
            return

        if not json_api_response:
            logging.error(f"Empty API response received at {response.url}")
            return

        hits = json_api_response.get("hits")
        if not hits:
            logging.error(f"No items found at {response.url}")
            return

        if len(hits) >= self.HIT_PER_PAGES:
            if end_timestamp - start_timestamp > 1:
                mid_timestamp = (start_timestamp + end_timestamp) // 2
                yield self._window_request(start_timestamp, mid_timestamp)
                yield self._window_request(mid_timestamp, end_timestamp)
                return
        logging.warning(
            f"1-second window still full for {start_timestamp}, keeping the first 1000"
        )

        for hit in hits:
            yield {
                "id": hit.get("objectID", ""),
                "title": hit.get("title"),
                "url": hit.get("url"),
                "points": hit.get("points"),
                "num_comments": hit.get("num_comments"),
                "author": hit.get("author"),
                "created_at_i": hit.get("created_at_i"),
                "tags": hit.get("_tags"),
            }

    def _iso_dates_to_unix_timestamps(self) -> tuple[int, int]:
        """Convert ISO start_date and end_date to UNIX for API call to HN's Algolia endpoints"""

        start_dt = datetime.fromisoformat(self.START_DATE.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(self.END_DATE.replace("Z", "+00:00"))

        start_dt = start_dt.replace(tzinfo=timezone.utc)
        end_dt = end_dt.replace(tzinfo=timezone.utc)

        start_timestamp = int(start_dt.timestamp())
        end_timestamp = int(end_dt.timestamp())

        if end_timestamp < start_timestamp:
            raise ValueError(
                f"Your end_date comes before your start_date. Double-check your input time again..."
            )

        return start_timestamp, end_timestamp
