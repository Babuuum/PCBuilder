import asyncio
import ipaddress
import json
import threading
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlparse

from invisible_playwright.async_api import InvisiblePlaywright

from app.core.logging import get_logger

logger = get_logger(__name__)
ResultT = TypeVar("ResultT")


class BrowserParserError(Exception):
    """Expected browser/parser failure safe to expose only by its public message."""


class DnsRetryableError(BrowserParserError):
    """Marker for failures that may safely be retried with another identity."""


@dataclass(frozen=True)
class ProxyConfig:
    server: str
    username: str | None = None
    password: str | None = None

    def as_playwright_proxy(self) -> dict[str, str]:
        parsed = urlparse(self.server)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
            raise ValueError("proxy server must use http, https or socks5 with a hostname")
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost":
            raise ValueError("local proxy targets are not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private or address.is_loopback or address.is_link_local
        ):
            raise ValueError("private proxy targets are not allowed")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("proxy credentials must be supplied as separate fields")
        proxy = {"server": self.server}
        if self.username is not None:
            proxy["username"] = self.username
        if self.password is not None:
            proxy["password"] = self.password
        return proxy


@dataclass(frozen=True)
class BrowserIdentity:
    seed: int
    proxy: ProxyConfig | None = None
    locale: str = "ru-RU"
    timezone: str = "Europe/Moscow"


class BrowserIdentityProvider(ABC):
    @abstractmethod
    async def acquire(self) -> BrowserIdentity:
        """Return the next complete proxy/fingerprint identity."""

    async def report_failure(self, identity: BrowserIdentity) -> None:
        """Hook for a future persistent proxy health store."""
        return None


class InMemoryBrowserIdentityProvider(BrowserIdentityProvider):
    def __init__(self, identities: Sequence[BrowserIdentity] | None = None) -> None:
        self._identities = (BrowserIdentity(seed=1),) if identities is None else tuple(identities)
        if not self._identities:
            raise ValueError("At least one browser identity is required")
        self._index = 0
        # The provider can be shared by tasks running on different event loops
        # (Celery invokes asyncio.run per task), so an asyncio.Lock is unsafe.
        self._lock = threading.Lock()

    async def acquire(self) -> BrowserIdentity:
        with self._lock:
            identity = self._identities[self._index]
            self._index = (self._index + 1) % len(self._identities)
            return identity


class AbstractBrowserParser(ABC):
    def __init__(
        self,
        *,
        identity_provider: BrowserIdentityProvider | None = None,
        headless: bool = True,
        max_attempts: int = 3,
        retry_delays: Sequence[float] = (1, 3),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.identity_provider = identity_provider or InMemoryBrowserIdentityProvider()
        self.headless = headless
        self.max_attempts = max_attempts
        self.retry_delays = tuple(retry_delays)
        self.browser_manager: Any = None
        self.browser: Any = None
        self.page: Any = None

    @abstractmethod
    async def start_parse(
        self,
        part_name: str,
        *,
        region: str,
    ) -> list[dict[str, Any]]:
        """Run a provider-specific search."""

    async def run_in_browser(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            identity = await self.identity_provider.acquire()
            try:
                await self._open_browser(identity)
                return await operation()
            except Exception as exc:
                last_error = exc
                if isinstance(exc, BrowserParserError) and not isinstance(exc, DnsRetryableError):
                    raise
                await self.identity_provider.report_failure(identity)
                logger.warning(
                    "Browser parser attempt failed attempt=%s/%s error_type=%s",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                )
                if attempt >= self.max_attempts:
                    raise BrowserParserError("Browser parser failed") from None
                delay_index = min(attempt - 1, len(self.retry_delays) - 1)
                if self.retry_delays:
                    await asyncio.sleep(self.retry_delays[delay_index])
            finally:
                await self.close_browser()
        raise BrowserParserError("Browser parser failed") from last_error
    async def _open_browser(self, identity: BrowserIdentity) -> None:
        self.browser_manager = InvisiblePlaywright(
            seed=identity.seed,
            headless=self.headless,
            proxy=identity.proxy.as_playwright_proxy() if identity.proxy else None,
            humanize=True,
            locale=identity.locale,
            timezone=identity.timezone,
        )
        self.browser = await self.browser_manager.__aenter__()
        self.page = await self.browser.new_page()

    async def close_browser(self) -> None:
        if self.browser_manager is not None:
            try:
                await self.browser_manager.__aexit__(None, None, None)
            except Exception:
                logger.warning("Browser cleanup failed", exc_info=False)
        self.browser_manager = None
        self.browser = None
        self.page = None


def identity_provider_from_json(raw_value: str) -> InMemoryBrowserIdentityProvider:
    """Build the temporary identity pool used until a persistent model exists."""
    try:
        records = json.loads(raw_value)
        if not isinstance(records, list):
            raise ValueError
        identities = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError
            proxy_record = record.get("proxy")
            proxy = ProxyConfig(**proxy_record) if proxy_record is not None else None
            identities.append(
                BrowserIdentity(
                    seed=int(record["seed"]),
                    proxy=proxy,
                    locale=record.get("locale", "ru-RU"),
                    timezone=record.get("timezone", "Europe/Moscow"),
                )
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("DNS browser identities configuration is invalid") from exc
    return InMemoryBrowserIdentityProvider(identities or None)
