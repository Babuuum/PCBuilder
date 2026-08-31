import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from app.parsers import base
from app.parsers.base import (
    AbstractBrowserParser,
    BrowserIdentity,
    BrowserParserError,
    InMemoryBrowserIdentityProvider,
    ProxyConfig,
)
from app.parsers.dns_parsers import DnsProvider, DnsRegionError


class FakeProducts:
    def __init__(self, counts: list[int]) -> None:
        self.count = AsyncMock(side_effect=counts)

    def nth(self, index: int):
        return SimpleNamespace(index=index)


class BrowserTestParser(AbstractBrowserParser):
    async def start_parse(self, part_name: str, *, region: str):
        return await self.run_in_browser(lambda: AsyncMock(return_value=[])())


async def test_limit_stops_without_extra_scroll() -> None:
    parser = DnsProvider(limit=2)
    products = FakeProducts([3])
    parser._parse_product = AsyncMock(side_effect=lambda _, index: {"index": index})
    parser._load_next_batch = AsyncMock()

    result = await parser._parse_catalog(products, total=10)

    assert result == [{"index": 0}, {"index": 1}]
    parser._load_next_batch.assert_not_awaited()


async def test_limit_none_parses_until_catalog_stops() -> None:
    parser = DnsProvider(limit=None)
    products = FakeProducts([2])
    parser._parse_product = AsyncMock(side_effect=lambda _, index: {"index": index})
    parser._load_next_batch = AsyncMock(return_value=False)

    result = await parser._parse_catalog(products, total=None)

    assert result == [{"index": 0}, {"index": 1}]
    parser._load_next_batch.assert_awaited_once_with(products)


async def test_unknown_total_uses_bounded_stable_checks() -> None:
    parser = DnsProvider(limit=None, stable_checks=3, scroll_pause=0)
    parser.page = SimpleNamespace(wait_for_timeout=AsyncMock())
    products = FakeProducts([2, 2, 2, 2])
    parser._scroll_catalog_bottom = AsyncMock()

    assert await parser._load_next_batch(products) is False
    assert parser._scroll_catalog_bottom.await_count == 3


async def test_browser_retry_rotates_complete_identity_and_always_closes(monkeypatch) -> None:
    identities = (
        BrowserIdentity(seed=11, proxy=ProxyConfig("http://proxy-a", "user", "secret-a")),
        BrowserIdentity(seed=22, proxy=ProxyConfig("http://proxy-b", "user", "secret-b")),
    )
    provider = InMemoryBrowserIdentityProvider(identities)
    options: list[dict] = []
    managers = []

    class Manager:
        def __init__(self, launch_options):
            self.launch_options = launch_options
            self.closed = False

        async def __aenter__(self):
            if self.launch_options["seed"] == 11:
                raise RuntimeError("first identity failed")
            return SimpleNamespace(new_page=AsyncMock(return_value=object()))

        async def __aexit__(self, *_):
            self.closed = True

    def launcher(**launch_options):
        options.append(launch_options)
        manager = Manager(launch_options)
        managers.append(manager)
        return manager

    monkeypatch.setattr(base, "InvisiblePlaywright", launcher)
    parser = BrowserTestParser(identity_provider=provider, retry_delays=(0,), max_attempts=2)

    result = await parser.run_in_browser(AsyncMock(return_value=[{"ok": True}]))

    assert result == [{"ok": True}]
    assert [(item["seed"], item["proxy"]["server"]) for item in options] == [
        (11, "http://proxy-a"),
        (22, "http://proxy-b"),
    ]
    assert all(manager.closed for manager in managers)


async def test_identity_provider_keeps_seed_bound_to_proxy() -> None:
    identity = BrowserIdentity(seed=42, proxy=ProxyConfig("http://proxy"))
    provider = InMemoryBrowserIdentityProvider([identity])

    assert await provider.acquire() is identity
    assert await provider.acquire() is identity


async def test_proxy_credentials_are_not_logged(caplog, monkeypatch) -> None:
    identity = BrowserIdentity(
        seed=42,
        proxy=ProxyConfig("http://proxy", "private-user", "private-password"),
    )

    class BrokenManager:
        async def __aenter__(self):
            raise RuntimeError("private-user:private-password")

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr(base, "InvisiblePlaywright", lambda **_: BrokenManager())
    parser = BrowserTestParser(
        identity_provider=InMemoryBrowserIdentityProvider([identity]),
        max_attempts=1,
    )

    with caplog.at_level(logging.WARNING), pytest.raises(BrowserParserError):
        await parser.run_in_browser(AsyncMock())

    assert "private-user" not in caplog.text
    assert "private-password" not in caplog.text


async def test_default_region_is_confirmed_before_search() -> None:
    parser = DnsProvider(limit=1)
    events = Mock()
    parser._set_region = AsyncMock(side_effect=lambda _: events("set_region"))
    parser._goto = AsyncMock(side_effect=lambda _: events("search"))
    parser._verify_region = AsyncMock(side_effect=lambda _: events("verify"))
    parser._find_products = AsyncMock(return_value=(object(), 1))
    parser._parse_catalog = AsyncMock(return_value=[{"name": "CPU"}])

    async def run(operation):
        return await operation()

    parser.run_in_browser = run

    result = await parser.start_parse("processor")

    assert result == [{"name": "CPU"}]
    parser._set_region.assert_awaited_once_with("Санкт-Петербург")
    assert events.call_args_list == [
        call("search"),
        call("set_region"),
        call("search"),
        call("verify"),
    ]


async def test_custom_region_is_passed_without_loss() -> None:
    parser = DnsProvider(limit=1)
    parser.run_in_browser = AsyncMock(return_value=[])

    await parser.start_parse("processor", region="  Москва  ")

    operation = parser.run_in_browser.await_args.args[0]
    parser._goto = AsyncMock()
    parser._set_region = AsyncMock(side_effect=DnsRegionError("stop"))
    with pytest.raises(DnsRegionError):
        await operation()
    parser._set_region.assert_awaited_once_with("Москва")


async def test_unconfirmed_region_is_controlled_error() -> None:
    parser = DnsProvider(page_load_timeout=0)
    parser.page = SimpleNamespace(wait_for_timeout=AsyncMock())

    with pytest.raises(DnsRegionError, match="could not be confirmed"):
        await parser._verify_region("Санкт-Петербург")
