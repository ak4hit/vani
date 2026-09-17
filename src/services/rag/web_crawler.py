"""Scoped Web Crawler - Crawls business website bounded by depth, pages, size, and domain constraints."""

from dataclasses import dataclass
from typing import Dict, List, Set, Optional
from urllib.parse import urlparse, urljoin
import httpx
from bs4 import BeautifulSoup

from src.services.rag.sanitizer import sanitize_text
from src.utils.logger import get_logger

logger = get_logger("vani.rag.crawler")


@dataclass
class CrawledPage:
    url: str
    title: str
    content: str
    depth: int
    bytes_count: int


class ScopedWebCrawler:
    """Safely crawls a business website adhering to strict security and cost scope limits."""

    def __init__(
        self,
        max_depth: int = 2,
        max_pages: int = 50,
        max_page_bytes: int = 100 * 1024,  # 100KB per page
        request_timeout: float = 8.0
    ):
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.max_page_bytes = max_page_bytes
        self.request_timeout = request_timeout

    @staticmethod
    def is_same_domain(base_domain: str, target_url: str) -> bool:
        """Verify target_url matches the base_domain without leaving the site."""
        target_domain = urlparse(target_url).netloc.lower()
        base_domain = base_domain.lower()
        return target_domain == base_domain or target_domain.endswith(f".{base_domain}")

    def clean_html_content(self, html_bytes: bytes) -> tuple[str, str]:
        """Strip scripts, styles, navigations, footers, and extract clean text and title."""
        # Enforce page size limit
        truncated = html_bytes[:self.max_page_bytes]
        soup = BeautifulSoup(truncated, "html.parser")

        # Remove irrelevant non-content elements
        for element in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
            element.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled Page"
        raw_text = soup.get_text(separator=" ", strip=True)
        return title, sanitize_text(raw_text)

    async def crawl_url(self, start_url: str) -> List[CrawledPage]:
        """Crawl website starting from start_url up to max_depth and max_pages."""
        parsed_start = urlparse(start_url)
        if not parsed_start.scheme or not parsed_start.netloc:
            raise ValueError(f"Invalid starting URL: '{start_url}'")

        base_domain = parsed_start.netloc
        visited: Set[str] = set()
        crawled_pages: List[CrawledPage] = []
        queue: List[tuple[str, int]] = [(start_url, 0)]

        headers = {
            "User-Agent": "VaniBot/1.0 (+https://github.com/ak4hit/vani; voice receptionist crawler)"
        }

        async with httpx.AsyncClient(timeout=self.request_timeout, follow_redirects=True) as client:
            while queue and len(crawled_pages) < self.max_pages:
                current_url, depth = queue.pop(0)

                # Normalize URL
                clean_url = current_url.split("#")[0].rstrip("/")
                if clean_url in visited:
                    continue
                visited.add(clean_url)

                if depth > self.max_depth:
                    continue

                try:
                    logger.info(f"Crawling depth={depth} url='{clean_url}'")
                    resp = await client.get(clean_url, headers=headers)
                    if resp.status_code != 200:
                        continue

                    content_type = resp.headers.get("content-type", "").lower()
                    if "text/html" not in content_type:
                        continue

                    title, text_content = self.clean_html_content(resp.content)
                    if len(text_content) > 30:
                        crawled_pages.append(CrawledPage(
                            url=clean_url,
                            title=title,
                            content=text_content,
                            depth=depth,
                            bytes_count=len(resp.content)
                        ))

                    # Discover in-domain internal links if not at max depth
                    if depth < self.max_depth and len(crawled_pages) < self.max_pages:
                        soup = BeautifulSoup(resp.content[:self.max_page_bytes], "html.parser")
                        for a_tag in soup.find_all("a", href=True):
                            href = a_tag["href"].strip()
                            absolute_url = urljoin(clean_url, href)
                            parsed_target = urlparse(absolute_url)

                            if (
                                parsed_target.scheme in ["http", "https"]
                                and self.is_same_domain(base_domain, absolute_url)
                                and absolute_url.split("#")[0].rstrip("/") not in visited
                            ):
                                queue.append((absolute_url, depth + 1))

                except Exception as e:
                    logger.warning(f"Failed to crawl '{clean_url}': {e}")
                    continue

        logger.info(f"Crawl completed for '{start_url}': gathered {len(crawled_pages)} pages.")
        return crawled_pages
