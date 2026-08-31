import re
import time
from typing import Any
from urllib.parse import urlencode, urljoin

from app.core.logging import get_logger
from app.parsers.base import (
    AbstractBrowserParser,
    BrowserIdentityProvider,
    DnsRetryableError,
)

logger = get_logger(__name__)


class DnsParserError(DnsRetryableError):
    pass


class DnsRegionError(DnsParserError):
    pass


class DnsProvider(AbstractBrowserParser):
    BASE_URL = "https://www.dns-shop.ru"
    SEARCH_URL = f"{BASE_URL}/search/"
    DEFAULT_REGION = "Санкт-Петербург"
    PRODUCTS_CONTAINER = ".products-list__content"
    PRODUCT = '[data-id="product"]'
    REGION_DISPLAY_SELECTORS = (
        '[data-role="region-name"]',
        '[data-role="city-select"]',
        '[data-role="location"]',
        ".city-select__text",
        ".city-select",
        ".location-picker-link__city",
        ".location-picker-link",
        '[class*="location-picker"]',
    )
    REGION_TRIGGER_SELECTORS = (
        '[data-role="city-select"]',
        '[data-role="location"]',
        ".city-select",
        ".location-picker-link",
        ".header-top-menu__common-link",
    )
    REGION_INPUT_SELECTORS = (
        'input[placeholder*="город" i]',
        'input[placeholder*="насел" i]',
        'input[placeholder*="регион" i]',
        ".city-search input",
    )

    def __init__(
        self,
        limit: int | None = 20,
        *,
        identity_provider: BrowserIdentityProvider | None = None,
        page_load_timeout: float = 30,
        field_timeout: float = 3,
        scroll_pause: float = 1.5,
        stable_checks: int = 5,
        max_batches: int = 200,
        headless: bool = True,
        max_attempts: int = 3,
        retry_delays: tuple[float, ...] = (1, 3),
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive or None")
        if stable_checks < 1:
            raise ValueError("stable_checks must be positive")
        if max_batches < 1:
            raise ValueError("max_batches must be positive")
        super().__init__(
            identity_provider=identity_provider,
            headless=headless,
            max_attempts=max_attempts,
            retry_delays=retry_delays,
        )
        self.limit = limit
        self.page_load_timeout = page_load_timeout
        self.field_timeout = field_timeout
        self.scroll_pause = scroll_pause
        self.stable_checks = stable_checks
        self.max_batches = max_batches
        self.result: list[dict[str, Any]] = []

    async def start_parse(
        self,
        part_name: str,
        *,
        region: str = DEFAULT_REGION,
    ) -> list[dict[str, Any]]:
        started_at = time.perf_counter()
        self.result = []
        normalized_region = self._clean_region(region)
        logger.info(
            "DNS parser started query=%r region=%r mode=%s",
            part_name,
            normalized_region,
            "all" if self.limit is None else "limited",
        )

        async def parse() -> list[dict[str, Any]]:
            url = f"{self.SEARCH_URL}?{urlencode({'q': part_name})}"
            await self._goto(url)
            await self._set_region(normalized_region)
            await self._goto(url)
            await self._verify_region(normalized_region)
            products, total = await self._find_products()
            self.result = await self._parse_catalog(products, total)
            return self.result

        try:
            return await self.run_in_browser(parse)
        finally:
            logger.info(
                "DNS parser finished items=%s elapsed=%.2fs",
                len(self.result),
                time.perf_counter() - started_at,
            )

    async def _goto(self, url: str) -> None:
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=self._page_timeout_ms)
        except Exception as exc:
            raise DnsParserError("DNS page did not load") from exc

    async def _set_region(self, region: str) -> None:
        await self._dismiss_region_confirmation()
        await self._wait_for_region_surface()
        if await self._region_matches(region):
            return
        if not await self._click_first(self.REGION_TRIGGER_SELECTORS):
            raise DnsRegionError("DNS region selector was not found")

        region_input = await self._first_visible(self.REGION_INPUT_SELECTORS)
        if region_input is not None:
            try:
                await region_input.fill(region, timeout=self._field_timeout_ms)
            except Exception as exc:
                raise DnsRegionError("DNS region input is unavailable") from exc

        option = self.page.get_by_text(region, exact=True).last
        try:
            await option.wait_for(state="visible", timeout=self._page_timeout_ms)
            await option.click(timeout=self._field_timeout_ms)
            await self.page.wait_for_timeout(500)
        except Exception as exc:
            raise DnsRegionError("Requested DNS region was not found") from exc
        await self._verify_region(region)

    async def _wait_for_region_surface(self) -> None:
        """Wait for the dynamic header before deciding whether to open the picker."""
        deadline = time.monotonic() + min(self.page_load_timeout, 5)
        while time.monotonic() < deadline:
            for selector in self.REGION_DISPLAY_SELECTORS:
                try:
                    if await self.page.locator(selector).count():
                        return
                except Exception:
                    continue
            await self.page.wait_for_timeout(250)

    async def _dismiss_region_confirmation(self) -> None:
        for label in ("Да", "Верно"):
            button = self.page.get_by_role("button", name=label, exact=True)
            try:
                if await button.count() and await button.first.is_visible():
                    await button.first.click(timeout=self._field_timeout_ms)
                    await self.page.wait_for_timeout(300)
                    return
            except Exception:
                continue

    async def _verify_region(self, region: str) -> None:
        deadline = time.monotonic() + self.page_load_timeout
        while time.monotonic() < deadline:
            if await self._region_matches(region):
                return
            await self.page.wait_for_timeout(250)
        raise DnsRegionError("DNS region could not be confirmed")

    async def _region_matches(self, region: str) -> bool:
        expected = self._normalize_text(region)
        for selector in self.REGION_DISPLAY_SELECTORS:
            locator = self.page.locator(selector)
            try:
                for index in range(await locator.count()):
                    candidate = locator.nth(index)
                    if not await candidate.is_visible():
                        continue
                    actual = self._normalize_text(await candidate.inner_text())
                    if actual == expected:
                        return True
            except Exception:
                continue
        return False

    async def _find_products(self):
        container = self.page.locator(self.PRODUCTS_CONTAINER)
        try:
            await container.wait_for(state="attached", timeout=self._page_timeout_ms)
            products = container.locator(self.PRODUCT)
            if await products.count() > 0:
                await products.first.wait_for(state="attached", timeout=self._page_timeout_ms)
        except Exception as exc:
            raise DnsParserError("DNS products were not found") from exc
        return products, await self._get_total_products()

    async def _get_total_products(self) -> int | None:
        locator = self.page.locator('[data-role="items-count"].products-count').first
        try:
            text = await locator.inner_text(timeout=self._field_timeout_ms)
        except Exception:
            return None
        match = re.search(r"\d[\d\s]*", text)
        return int(re.sub(r"\s+", "", match.group())) if match else None

    async def _parse_catalog(self, products, total: int | None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        target = (
            min(self.limit, total) if self.limit is not None and total is not None else self.limit
        )
        for _ in range(self.max_batches):
            loaded = await products.count()
            parse_until = min(loaded, target) if target is not None else loaded
            while len(result) < parse_until:
                result.append(await self._parse_product(products.nth(len(result)), len(result)))
            if target is not None and len(result) >= target:
                return result
            if total is not None and loaded >= total:
                return result
            if not await self._load_next_batch(products):
                return result
        return result

    async def _load_next_batch(self, products) -> bool:
        previous_count = await products.count()
        if previous_count == 0:
            return False
        for _ in range(self.stable_checks):
            await self._scroll_catalog_bottom(products)
            await self.page.wait_for_timeout(int(self.scroll_pause * 1000))
            if await products.count() > previous_count:
                return True
        return False

    async def _scroll_catalog_bottom(self, products) -> None:
        count = await products.count()
        if count:
            try:
                await products.nth(count - 1).scroll_into_view_if_needed()
            except Exception:
                pass
        try:
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.mouse.wheel(0, 800)
        except Exception:
            pass

    async def _parse_product(self, product, index: int) -> dict[str, Any]:
        product_number = index + 1
        name_locator = product.locator("a.catalog-product__name").first
        title = await self._get_attribute(name_locator, "title", product_number, "name")
        href = await self._get_attribute(name_locator, "href", product_number, "href")
        product_rating = await self._get_text(
            product.locator("a.catalog-product__rating b").first,
            product_number,
            "product rating",
        )
        brand_rating = None
        if not product_rating:
            brand_rating = await self._get_text(
                product.locator("div.catalog-product__rating_brand b").first,
                product_number,
                "brand rating",
            )
        return {
            "name": title.split("[", 1)[0].strip() if title else None,
            "price": await self._get_text(
                product.locator(".product-buy__price").first, product_number, "price"
            ),
            "stock": await self._get_text(
                product.locator("a.order-avail-wrap__link").first, product_number, "stock"
            ),
            "href": urljoin(self.BASE_URL, href) if href else None,
            "rating": product_rating or brand_rating,
            "rating_type": "product" if product_rating else "brand" if brand_rating else None,
            "product_code": await self._get_attribute(
                product, "data-code", product_number, "product_code"
            ),
            "product_id": await self._get_attribute(
                product, "data-product", product_number, "product_id"
            ),
            "availability_status": await self._get_attribute(
                product, "data-avail-status", product_number, "availability_status"
            ),
        }

    async def _get_text(self, locator, product_number: int, field_name: str) -> str | None:
        try:
            value = await locator.inner_text(timeout=self._field_timeout_ms)
            return " ".join(value.split()) or None
        except Exception:
            logger.debug("DNS product field missing item=%s field=%s", product_number, field_name)
            return None

    async def _get_attribute(
        self, locator, attribute: str, product_number: int, field_name: str
    ) -> str | None:
        try:
            return await locator.get_attribute(attribute, timeout=self._field_timeout_ms) or None
        except Exception:
            logger.debug("DNS product field missing item=%s field=%s", product_number, field_name)
            return None

    async def _click_first(self, selectors: tuple[str, ...]) -> bool:
        locator = await self._first_visible(selectors)
        if locator is None:
            return False
        try:
            await locator.click(timeout=self._field_timeout_ms)
            return True
        except Exception:
            return False

    async def _first_visible(self, selectors: tuple[str, ...]):
        for selector in selectors:
            locator = self.page.locator(selector)
            try:
                for index in range(await locator.count()):
                    candidate = locator.nth(index)
                    if await candidate.is_visible():
                        return candidate
            except Exception:
                continue
        return None

    @staticmethod
    def _clean_region(region: str) -> str:
        cleaned = " ".join(region.split())
        if not cleaned:
            raise ValueError("region must not be blank")
        return cleaned

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.split()).casefold().replace("ё", "е")

    @property
    def _page_timeout_ms(self) -> int:
        return int(self.page_load_timeout * 1000)

    @property
    def _field_timeout_ms(self) -> int:
        return int(self.field_timeout * 1000)
