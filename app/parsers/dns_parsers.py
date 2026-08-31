import asyncio
import re
import time
from urllib.parse import urlencode, urljoin

from cloakbrowser import launch_async

from app.core.logging import get_logger

logger = get_logger(__name__)


class DnsParserError(Exception):
    pass


class DnsProvider:
    BASE_URL = "https://www.dns-shop.ru"
    SEARCH_URL = "https://www.dns-shop.ru/search/"

    MAX_ATTEMPTS = 5

    RETRY_DELAYS = (
        5,
        10,
        30,
        60,
    )

    def __init__(
        self,
        component_counts: int = 20,
        search_depth: int = 20,
        page_load_timeout: int = 6000,
        field_timeout: float = 3.0,
        scroll_pause: float = 1.5,
        full_mode_stable_checks: int = 20,
    ):
        """
        component_counts:
            > 0 -> максимальное количество товаров
            0   -> количество товаров не ограничено

        search_depth:
            > 0 -> максимальное количество подгрузок
            0   -> количество подгрузок не ограничено

        page_load_timeout:
            timeout первоначальной загрузки страницы.
            Используется только для:
                - page.goto()
                - title
                - products container
                - first product

            В секундах.

        field_timeout:
            сколько ждать отдельное поле товара:
                - name
                - price
                - stock
                - href
                - rating
                - attributes

            В секундах.

            Если поле не появилось -> None.

        scroll_pause:
            задержка между проверками появления
            новой пачки товаров.

        full_mode_stable_checks:
            используется только если DNS не удалось
            получить общее количество товаров.

            Например 10:
                если после 10 последовательных проверок
                количество товаров не изменилось,
                считаем каталог полностью загруженным.

        FULL MODE:
            component_counts = 0
            search_depth = 0

            Сначала полностью загружает каталог,
            потом парсит все карточки.
        """

        self.component_counts = component_counts
        self.search_depth = search_depth

        self.page_load_timeout = page_load_timeout
        self.field_timeout = field_timeout

        self.scroll_pause = scroll_pause

        self.full_mode_stable_checks = (
            full_mode_stable_checks
        )

        self.result: list[dict] = []

        self.page = None
        self.browser = None

    @property
    def parse_all(self) -> bool:
        return (
            self.component_counts == 0
            and self.search_depth == 0
        )

    # =====================================================
    # PUBLIC
    # =====================================================

    async def start_parse(
        self,
        part_name: str,
    ) -> list[dict]:

        started_at = time.perf_counter()

        params = urlencode(
            {
                "q": part_name,
            }
        )

        url = f"{self.SEARCH_URL}?{params}"

        logger.info(
            f"[DNS] query: {part_name}"
        )

        logger.info(
            f"[DNS] url: {url}"
        )

        logger.info(
            f"[DNS] parse all: "
            f"{self.parse_all}"
        )

        logger.info(
            f"[DNS] page load timeout: "
            f"{self.page_load_timeout}s"
        )

        logger.info(
            f"[DNS] field timeout: "
            f"{self.field_timeout}s"
        )

        self.result = []

        products = None
        total_products = None

        try:

            # =================================================
            # INITIAL PAGE LOAD
            #
            # Retry используется ТОЛЬКО здесь.
            # =================================================

            for attempt in range(
                1,
                self.MAX_ATTEMPTS + 1,
            ):
                try:
                    logger.info(
                        f"\n[DNS] initial load attempt "
                        f"{attempt}/"
                        f"{self.MAX_ATTEMPTS}"
                    )

                    self.browser = (
                        await launch_async(
                            headless=False,
                            humanize=True,
                        )
                    )

                    self.page = (
                        await self.browser.new_page()
                    )

                    (
                        products,
                        total_products,
                    ) = await self.__initial_load(
                        url
                    )

                    # Страница успешно открыта.
                    # Больше retry не нужен.
                    break

                except DnsParserError as exc:
                    logger.info(
                        f"[DNS] initial load error: "
                        f"{exc}"
                    )

                    await self.__close_browser()

                    if (
                        attempt
                        >= self.MAX_ATTEMPTS
                    ):
                        raise

                    delay = self.RETRY_DELAYS[
                        attempt - 1
                    ]

                    logger.info(
                        f"[DNS] retry after "
                        f"{delay}s"
                    )

                    await asyncio.sleep(
                        delay
                    )

                except Exception:
                    await self.__close_browser()
                    raise

            if products is None:
                raise DnsParserError(
                    "Products locator was not initialized"
                )

            # =================================================
            # FULL MODE
            # =================================================

            if self.parse_all:
                await self.__load_all_products(
                    products=products,
                    total_products=total_products,
                )

                loaded_count = (
                    await products.count()
                )

                logger.info("")
                logger.info(
                    f"[DNS] all products loaded: "
                    f"{loaded_count}"
                )

                logger.info(
                    "[DNS] starting product parsing..."
                )

                self.result = (
                    await self.__parse_loaded_products(
                        products=products,
                        limit=loaded_count,
                    )
                )

            # =================================================
            # NORMAL MODE
            # =================================================

            else:
                self.result = (
                    await self.__parse_with_loading(
                        products=products,
                        total_products=total_products,
                    )
                )

            return self.result

        finally:
            await self.__close_browser()

            elapsed = (
                time.perf_counter()
                - started_at
            )

            minutes = elapsed / 60

            logger.info("")
            logger.info(
                "================================"
            )

            logger.info(
                "[DNS] parser finished"
            )

            logger.info(
                f"[DNS] execution time: "
                f"{elapsed:.2f}s "
                f"({minutes:.2f} min)"
            )

            logger.info(
                f"[DNS] parsed products: "
                f"{len(self.result)}"
            )

            logger.info(
                "================================"
            )

    # =====================================================
    # INITIAL LOAD
    # =====================================================

    async def __initial_load(
        self,
        url: str,
    ):
        """
        Единственная стадия, где используется
        page_load_timeout.

        После появления первой карточки
        общего таймаута больше нет.
        """

        timeout_ms = int(
            self.page_load_timeout * 1000
        )

        # =================================================
        # PAGE
        # =================================================

        try:
            await self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )

        except Exception as exc:
            raise DnsParserError(
                f"Page did not load within "
                f"{self.page_load_timeout}s"
            ) from exc

        # =================================================
        # TITLE
        # =================================================

        try:
            await self.page.wait_for_function(
                """
                () => (
                    document.title &&
                    document.title.trim().length > 0
                )
                """,
                timeout=timeout_ms,
            )

            title = (
                await self.page.title()
            )

            logger.info(
                f"[DNS] title: {title}"
            )

        except Exception as exc:
            raise DnsParserError(
                f"Page title did not appear within "
                f"{self.page_load_timeout}s"
            ) from exc

        # =================================================
        # PRODUCTS CONTAINER
        # =================================================

        container = self.page.locator(
            ".products-list__content"
        )

        try:
            await container.wait_for(
                state="attached",
                timeout=timeout_ms,
            )

        except Exception as exc:
            raise DnsParserError(
                f"Products container did not appear "
                f"within {self.page_load_timeout}s"
            ) from exc

        # =================================================
        # PRODUCTS
        # =================================================

        products = container.locator(
            '[data-id="product"]'
        )

        # =================================================
        # FIRST PRODUCT
        # =================================================

        try:
            await products.first.wait_for(
                state="attached",
                timeout=timeout_ms,
            )

        except Exception as exc:
            raise DnsParserError(
                f"First product did not appear within "
                f"{self.page_load_timeout}s"
            ) from exc

        initial_count = (
            await products.count()
        )

        logger.info(
            f"[DNS] initially loaded: "
            f"{initial_count}"
        )

        # =================================================
        # TOTAL PRODUCTS
        # =================================================

        total_products = (
            await self.__get_total_products()
        )

        logger.info(
            f"[DNS] available products: "
            f"{total_products}"
        )

        return (
            products,
            total_products,
        )

    # =====================================================
    # TOTAL COUNT
    # =====================================================

    async def __get_total_products(
        self,
    ) -> int | None:
        """
        Пример:

            742 товара
            1 245 товаров

        -> 742
        -> 1245

        Если DNS не отдал счётчик,
        возвращаем None.

        Это НЕ является ошибкой.
        """

        timeout_ms = int(
            self.field_timeout * 1000
        )

        try:
            locator = self.page.locator(
                '[data-role="items-count"].products-count'
            ).first

            await locator.wait_for(
                state="attached",
                timeout=timeout_ms,
            )

            text = await locator.inner_text(
                timeout=timeout_ms,
            )

            if not text:
                return None

            match = re.search(
                r"\d[\d\s]*",
                text,
            )

            if not match:
                return None

            number = re.sub(
                r"\s+",
                "",
                match.group(),
            )

            return int(number)

        except Exception:
            return None

    # =====================================================
    # NORMAL MODE
    # =====================================================

    async def __parse_with_loading(
        self,
        products,
        total_products: int | None,
    ) -> list[dict]:

        result = []

        parsed_count = 0
        load_count = 0

        # =================================================
        # TARGET COUNT
        # =================================================

        target_count = (
            self.component_counts
        )

        if (
            total_products is not None
            and target_count > 0
        ):
            target_count = min(
                target_count,
                total_products,
            )

        logger.info(
            f"[DNS] target products: "
            f"{target_count}"
        )

        while True:

            current_count = (
                await products.count()
            )

            logger.info(
                f"[DNS] loaded: "
                f"{current_count}, "
                f"parsed: {parsed_count}, "
                f"loads: {load_count}"
            )

            # =================================================
            # PARSE UNTIL
            # =================================================

            if target_count > 0:
                parse_until = min(
                    current_count,
                    target_count,
                )
            else:
                parse_until = (
                    current_count
                )

            # =================================================
            # PARSE NEW PRODUCTS
            # =================================================

            while (
                parsed_count
                < parse_until
            ):
                product = products.nth(
                    parsed_count
                )

                data = (
                    await self.__parse_product(
                        product=product,
                        index=parsed_count,
                    )
                )

                result.append(
                    data
                )

                self.__print_product(
                    data,
                    parsed_count,
                )

                parsed_count += 1

            # =================================================
            # TARGET REACHED
            # =================================================

            if (
                target_count > 0
                and parsed_count
                >= target_count
            ):
                logger.info(
                    f"[DNS] target reached: "
                    f"{parsed_count}/"
                    f"{target_count}"
                )

                return result

            # =================================================
            # WHOLE CATALOG
            # =================================================

            if (
                total_products is not None
                and current_count
                >= total_products
            ):
                logger.info(
                    f"[DNS] whole catalog loaded: "
                    f"{current_count}/"
                    f"{total_products}"
                )

                return result

            # =================================================
            # SEARCH DEPTH
            # =================================================

            if (
                self.search_depth > 0
                and load_count
                >= self.search_depth
            ):
                logger.info(
                    f"[DNS] search depth reached: "
                    f"{load_count}"
                )

                return result

            # =================================================
            # LOAD NEXT BATCH
            # =================================================

            new_products_loaded = (
                await self.__load_next_batch(
                    products=products,

                    # Если общее количество известно,
                    # ждём новую пачку сколько потребуется.
                    #
                    # Если неизвестно — нужен fallback
                    # для определения конца каталога.
                    allow_stable_stop=(
                        total_products is None
                    ),
                )
            )

            load_count += 1

            if not new_products_loaded:
                logger.info(
                    "[DNS] no more products"
                )

                return result

    # =====================================================
    # FULL MODE
    # =====================================================

    async def __load_all_products(
        self,
        products,
        total_products: int | None,
    ):
        """
        FULL MODE.

        Сначала загружается весь каталог.

        Если total_products известен:
            ждём, пока products.count()
            достигнет total_products.

        Если total_products неизвестен:
            используем fallback:
            несколько последовательных проверок
            без увеличения числа товаров.

        Никакого общего timeout нет.
        """

        load_count = 0

        if total_products is None:
            logger.info(
                "[DNS][ALL] total product count "
                "is unknown"
            )

            logger.info(
                "[DNS][ALL] fallback to "
                "stable catalog detection"
            )

        else:
            logger.info(
                f"[DNS][ALL] expected total: "
                f"{total_products}"
            )

        while True:

            current_count = (
                await products.count()
            )

            if total_products is not None:
                logger.info(
                    f"[DNS][ALL] loaded: "
                    f"{current_count}/"
                    f"{total_products}"
                )

            else:
                logger.info(
                    f"[DNS][ALL] loaded: "
                    f"{current_count}"
                )

            # =================================================
            # KNOWN TOTAL
            # =================================================

            if (
                total_products is not None
                and current_count
                >= total_products
            ):
                logger.info(
                    "[DNS][ALL] "
                    "whole catalog loaded"
                )

                return

            # =================================================
            # LOAD
            # =================================================

            loaded = (
                await self.__load_next_batch(
                    products=products,

                    allow_stable_stop=(
                        total_products is None
                    ),
                )
            )

            load_count += 1

            # =================================================
            # UNKNOWN TOTAL + NO NEW PRODUCTS
            # =================================================

            if not loaded:
                final_count = (
                    await products.count()
                )

                logger.info(
                    "[DNS][ALL] catalog "
                    "stopped growing"
                )

                logger.info(
                    f"[DNS][ALL] final loaded: "
                    f"{final_count}"
                )

                return

    # =====================================================
    # LOAD NEXT BATCH
    # =====================================================

    async def __load_next_batch(
        self,
        products,
        allow_stable_stop: bool,
    ) -> bool:
        """
        Загружает следующую пачку.

        Если allow_stable_stop=False:
            никакого timeout / лимита ожидания нет.
            Ждём новую пачку сколько потребуется.

        Если allow_stable_stop=True:
            используется full_mode_stable_checks,
            потому что иначе при неизвестном общем
            количестве невозможно определить конец.
        """

        previous_count = (
            await products.count()
        )

        if previous_count == 0:
            return False

        logger.info(
            f"[DNS] loading next batch "
            f"after {previous_count} products..."
        )

        stable_checks = 0

        # Первоначально доходим до конца
        await self.__scroll_catalog_bottom(
            products
        )

        while True:

            await self.page.wait_for_timeout(
                int(
                    self.scroll_pause
                    * 1000
                )
            )

            current_count = (
                await products.count()
            )

            # =================================================
            # NEW PRODUCTS
            # =================================================

            if (
                current_count
                > previous_count
            ):
                logger.info(
                    f"[DNS] new batch loaded: "
                    f"{previous_count} -> "
                    f"{current_count}"
                )

                return True

            # =================================================
            # UNKNOWN TOTAL
            # =================================================

            if allow_stable_stop:
                stable_checks += 1

                logger.info(
                    f"[DNS] waiting for products: "
                    f"{stable_checks}/"
                    f"{self.full_mode_stable_checks}"
                )

                if (
                    stable_checks
                    >= self.full_mode_stable_checks
                ):
                    return False

            # =================================================
            # CONTINUE SCROLLING
            # =================================================

            await self.__scroll_catalog_bottom(
                products
            )

    # =====================================================
    # SCROLL
    # =====================================================

    async def __scroll_catalog_bottom(
        self,
        products,
    ):
        count = (
            await products.count()
        )

        if count == 0:
            return

        # =================================================
        # LAST PRODUCT
        # =================================================

        try:
            last_product = products.nth(
                count - 1
            )

            await (
                last_product
                .scroll_into_view_if_needed()
            )

        except Exception:
            pass

        # =================================================
        # PAGINATION CONTAINER
        # =================================================

        try:
            pagination = self.page.locator(
                ".pagination-container"
            ).first

            if (
                await pagination.count()
                > 0
            ):
                await (
                    pagination
                    .scroll_into_view_if_needed()
                )

        except Exception:
            pass

        # =================================================
        # PAGE BOTTOM
        # =================================================

        try:
            await self.page.evaluate(
                """
                () => {
                    window.scrollTo(
                        0,
                        document.body.scrollHeight
                    );
                }
                """
            )

        except Exception:
            pass

        # Дополнительный wheel для DNS lazy-load
        try:
            await self.page.mouse.wheel(
                0,
                800,
            )

        except Exception:
            pass

    # =====================================================
    # PARSE PRELOADED PRODUCTS
    # =====================================================

    async def __parse_loaded_products(
        self,
        products,
        limit: int,
    ) -> list[dict]:

        result = []

        for index in range(
            limit
        ):
            product = products.nth(
                index
            )

            data = (
                await self.__parse_product(
                    product=product,
                    index=index,
                )
            )

            result.append(
                data
            )

            self.__print_product(
                data,
                index,
            )

        return result

    # =====================================================
    # SAFE FIELD HELPERS
    # =====================================================

    async def __get_text(
        self,
        locator,
        product_number: int,
        field_name: str,
    ) -> str | None:

        timeout_ms = int(
            self.field_timeout * 1000
        )

        try:
            await locator.wait_for(
                state="attached",
                timeout=timeout_ms,
            )

            value = await locator.inner_text(
                timeout=timeout_ms,
            )

            value = " ".join(
                value.split()
            )

            return value or None

        except Exception:
            logger.info(
                f"[DNS][{product_number}] "
                f"{field_name} not found"
            )

            return None

    async def __get_attribute(
        self,
        locator,
        attribute: str,
        product_number: int,
        field_name: str,
    ) -> str | None:

        timeout_ms = int(
            self.field_timeout * 1000
        )

        try:
            await locator.wait_for(
                state="attached",
                timeout=timeout_ms,
            )

            value = await locator.get_attribute(
                attribute,
                timeout=timeout_ms,
            )

            return value or None

        except Exception:
            logger.info(
                f"[DNS][{product_number}] "
                f"{field_name} not found"
            )

            return None

    # =====================================================
    # PRODUCT PARSER
    # =====================================================

    async def __parse_product(
        self,
        product,
        index: int,
    ) -> dict:

        # Человеческая нумерация:
        #
        # index 0 -> PRODUCT 1
        # index 51 -> PRODUCT 52

        product_number = (
            index + 1
        )

        data = {
            "name": None,
            "price": None,
            "stock": None,
            "href": None,
            "rating": None,
            "rating_type": None,
            "product_code": None,
            "product_id": None,
            "availability_status": None,
        }

        # =================================================
        # NAME LOCATOR
        # =================================================

        name_locator = product.locator(
            "a.catalog-product__name"
        ).first

        # =================================================
        # NAME
        # =================================================

        title = (
            await self.__get_attribute(
                locator=name_locator,
                attribute="title",
                product_number=product_number,
                field_name="name",
            )
        )

        if title:
            data["name"] = (
                title
                .split("[", 1)[0]
                .strip()
            )

        # =================================================
        # PRICE
        # =================================================

        data["price"] = (
            await self.__get_text(
                locator=product.locator(
                    ".product-buy__price"
                ).first,
                product_number=product_number,
                field_name="price",
            )
        )

        # =================================================
        # STOCK
        # =================================================

        data["stock"] = (
            await self.__get_text(
                locator=product.locator(
                    "a.order-avail-wrap__link"
                ).first,
                product_number=product_number,
                field_name="stock",
            )
        )

        # =================================================
        # HREF
        # =================================================

        href = (
            await self.__get_attribute(
                locator=name_locator,
                attribute="href",
                product_number=product_number,
                field_name="href",
            )
        )

        if href:
            data["href"] = urljoin(
                self.BASE_URL,
                href,
            )

        # =================================================
        # RATING
        # =================================================

        product_rating = (
            await self.__get_text(
                locator=product.locator(
                    "a.catalog-product__rating b"
                ).first,
                product_number=product_number,
                field_name="product rating",
            )
        )

        if product_rating:
            data["rating"] = (
                product_rating
            )

            data["rating_type"] = (
                "product"
            )

        else:
            # =================================================
            # BRAND RATING
            # =================================================

            brand_rating = (
                await self.__get_text(
                    locator=product.locator(
                        "div."
                        "catalog-product__rating_brand "
                        "b"
                    ).first,
                    product_number=product_number,
                    field_name="brand rating",
                )
            )

            if brand_rating:
                data["rating"] = (
                    brand_rating
                )

                data["rating_type"] = (
                    "brand"
                )

        # =================================================
        # PRODUCT CODE
        # =================================================

        data["product_code"] = (
            await self.__get_attribute(
                locator=product,
                attribute="data-code",
                product_number=product_number,
                field_name="product_code",
            )
        )

        # =================================================
        # PRODUCT ID
        # =================================================

        data["product_id"] = (
            await self.__get_attribute(
                locator=product,
                attribute="data-product",
                product_number=product_number,
                field_name="product_id",
            )
        )

        # =================================================
        # AVAILABILITY STATUS
        # =================================================

        data["availability_status"] = (
            await self.__get_attribute(
                locator=product,
                attribute="data-avail-status",
                product_number=product_number,
                field_name="availability_status",
            )
        )

        return data

    # =====================================================
    # BROWSER
    # =====================================================

    async def __close_browser(
        self,
    ):
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass

        self.browser = None
        self.page = None

    # =====================================================
    # LOG
    # =====================================================

    @staticmethod
    def __print_product(
        product: dict,
        index: int,
    ):
        product_number = (
            index + 1
        )

        logger.info("")
        logger.info(
            f"========== PRODUCT "
            f"{product_number} =========="
        )

        logger.info(
            f"name: "
            f"{product.get('name')}"
        )

        logger.info(
            f"price: "
            f"{product.get('price')}"
        )

        logger.info(
            f"stock: "
            f"{product.get('stock')}"
        )

        logger.info(
            f"rating: "
            f"{product.get('rating')}"
        )

        logger.info(
            f"rating_type: "
            f"{product.get('rating_type')}"
        )

        logger.info(
            f"href: "
            f"{product.get('href')}"
        )

        logger.info(
            f"product_code: "
            f"{product.get('product_code')}"
        )

        logger.info(
            f"product_id: "
            f"{product.get('product_id')}"
        )

        logger.info(
            f"availability_status: "
            f"{product.get('availability_status')}"
        )
