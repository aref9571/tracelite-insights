from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

CHECKOUT = "checkout-service"
CATALOG = "catalog-service"
AUTH = "auth-service"
PRODUCTION = "production"
STAGING = "staging"

WARM_UP_HOURS = 24
INCIDENT_COUNT = 10
HARD_INCIDENT_COUNT = 3
MAX_INCIDENT_HOURS = 6
MAX_HARD_INCIDENT_HOURS = 2
MIN_MULTIPLIER = 4.0
MAX_HARD_MULTIPLIER = 6.0
MAX_MULTIPLIER = 20.0
MINIMUM_DAYS = 4


@dataclass(frozen=True)
class Family:
    id: str
    description: str


@dataclass(frozen=True)
class FailureType:
    id: str
    application: str
    environment: str
    exception_type: str
    message_template: str
    stack_frames: tuple[str, ...]
    base_rate_per_hour: float
    family_id: str | None = None


@dataclass(frozen=True)
class Incident:
    id: str
    application: str
    environment: str
    failure_ids: tuple[str, ...]
    start: datetime
    end: datetime
    multiplier: float

    def covers(self, failure_id: str, hour_start: datetime) -> bool:
        return failure_id in self.failure_ids and self.start <= hour_start < self.end


@dataclass(frozen=True)
class Catalogue:
    families: tuple[Family, ...]
    failure_types: tuple[FailureType, ...]
    incidents: tuple[Incident, ...]


def frames(*methods_with_lines: str) -> tuple[str, ...]:
    return tuple(java_frame(entry) for entry in methods_with_lines)


def java_frame(method_with_line: str) -> str:
    method, line = method_with_line.rsplit(":", 1)
    class_name = method.rsplit(".", 1)[0]
    file_name = class_name.rsplit(".", 1)[-1].split("$")[0] + ".java"
    return f"{method}({file_name}:{line})"


SPRING_WEB = (
    "org.springframework.web.servlet.FrameworkServlet.service:897",
    "jakarta.servlet.http.HttpServlet.service:658",
)
SPRING_LISTENER = (
    "org.springframework.amqp.rabbit.listener.AbstractMessageListenerContainer"
    ".doInvokeListener:1660",
    "org.springframework.amqp.rabbit.listener.SimpleMessageListenerContainer"
    ".doReceiveAndExecute:1032",
)
HIKARI = (
    "com.zaxxer.hikari.pool.HikariPool.getConnection:181",
    "com.zaxxer.hikari.HikariDataSource.getConnection:128",
)

FAMILIES = (
    Family("db-outage", "Primary checkout database stops accepting connections"),
    Family("payment-gateway", "Payment provider answers too slowly"),
    Family("catalog-cache", "Redis cache of the catalog is unreachable"),
    Family("search-cluster", "Search cluster loses its shards"),
    Family("image-cdn", "Image CDN returns errors for product pictures"),
    Family("signing-key-rotation", "Token signing key rotated without updating verifiers"),
    Family("directory-outage", "LDAP directory server is unreachable"),
    Family("session-store", "Redis session store of the auth service is unreachable"),
    Family("inventory-latency", "Inventory service in staging responds slowly"),
    Family("message-broker", "RabbitMQ broker is down for publishers and listeners"),
    Family("staging-migration", "Staging database schema migration missing a column"),
    Family("login-rate-limit", "Login rate limit configured too low in staging"),
)

FAMILY_MEMBERS = (
    FailureType(
        id="checkout-db-connection-refused",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="org.postgresql.util.PSQLException",
        message_template="Connection to db-{node}.internal:5432 refused after {ms} ms",
        stack_frames=frames(
            "org.postgresql.core.v3.ConnectionFactoryImpl.openConnectionImpl:346",
            *HIKARI,
            "com.example.checkout.order.OrderRepository.save:64",
            "com.example.checkout.order.OrderService.placeOrder:118",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.3,
        family_id="db-outage",
    ),
    FailureType(
        id="checkout-db-pool-timeout",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="java.sql.SQLTransientConnectionException",
        message_template=(
            "HikariPool-1 - Connection is not available, request timed out after {ms}ms."
        ),
        stack_frames=frames(
            *HIKARI,
            "com.example.checkout.payment.PaymentRepository.findByOrderId:41",
            "com.example.checkout.payment.PaymentService.status:77",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.5,
        family_id="db-outage",
    ),
    FailureType(
        id="checkout-db-resource-failure",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="org.springframework.dao.DataAccessResourceFailureException",
        message_template="Unable to acquire JDBC Connection for order {order_id}",
        stack_frames=frames(
            "org.springframework.jdbc.datasource.DataSourceUtils.getConnection:84",
            "com.example.checkout.cart.CartRepository.load:52",
            "com.example.checkout.cart.CartService.currentCart:39",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.2,
        family_id="db-outage",
    ),
    FailureType(
        id="checkout-payment-read-timeout",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="java.net.SocketTimeoutException",
        message_template="Read timed out after {ms} ms",
        stack_frames=frames(
            "java.net.http.HttpClient.send:412",
            "com.example.checkout.payment.PaymentClient.charge:87",
            "com.example.checkout.order.OrderService.placeOrder:131",
            *SPRING_WEB,
        ),
        base_rate_per_hour=1.2,
        family_id="payment-gateway",
    ),
    FailureType(
        id="checkout-refund-resource-access",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="org.springframework.web.client.ResourceAccessException",
        message_template=(
            'I/O error on POST request for "https://payments.example.com/v2/refunds": '
            "Read timed out after {ms} ms"
        ),
        stack_frames=frames(
            "org.springframework.web.client.RestTemplate.doExecute:915",
            "com.example.checkout.payment.RefundClient.refund:58",
            "com.example.checkout.payment.RefundService.refundOrder:44",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.4,
        family_id="payment-gateway",
    ),
    FailureType(
        id="checkout-payment-failed",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="com.example.checkout.payment.PaymentFailedException",
        message_template="Payment {payment_id} for order {order_id} failed: gateway timeout",
        stack_frames=frames(
            "com.example.checkout.payment.PaymentService.capture:103",
            "com.example.checkout.order.OrderService.placeOrder:135",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.8,
        family_id="payment-gateway",
    ),
    FailureType(
        id="catalog-cache-connection-failure",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="org.springframework.data.redis.RedisConnectionFailureException",
        message_template="Unable to connect to Redis at cache-{node}.internal:6379",
        stack_frames=frames(
            "org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory"
            ".getConnection:1263",
            "com.example.catalog.product.ProductCache.get:36",
            "com.example.catalog.product.ProductService.findBySku:58",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.6,
        family_id="catalog-cache",
    ),
    FailureType(
        id="catalog-cache-command-timeout",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="io.lettuce.core.RedisCommandTimeoutException",
        message_template="Command timed out after {ms} millisecond(s)",
        stack_frames=frames(
            "io.lettuce.core.internal.ExceptionFactory.createTimeoutException:63",
            "com.example.catalog.price.PriceCache.lookup:48",
            "com.example.catalog.price.PriceService.currentPrice:71",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.9,
        family_id="catalog-cache",
    ),
    FailureType(
        id="catalog-search-shards-failed",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type=(
            "org.springframework.data.elasticsearch.UncategorizedElasticsearchException"
        ),
        message_template=(
            "[es/search] failed: [search_phase_execution_exception] all shards failed "
            "for query {query}"
        ),
        stack_frames=frames(
            "org.springframework.data.elasticsearch.client.elc.ElasticsearchTemplate.search:345",
            "com.example.catalog.search.ProductSearch.search:62",
            "com.example.catalog.search.SearchController.search:29",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.7,
        family_id="search-cluster",
    ),
    FailureType(
        id="catalog-search-connection-refused",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="java.net.ConnectException",
        message_template="Connection refused: search-{node}.internal/{ip}:9200",
        stack_frames=frames(
            "sun.nio.ch.Net.pollConnect:682",
            "org.apache.http.impl.nio.reactor.DefaultConnectingIOReactor.processEvent:174",
            "com.example.catalog.search.SuggestionClient.suggest:40",
            "com.example.catalog.search.SearchController.suggest:44",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.3,
        family_id="search-cluster",
    ),
    FailureType(
        id="catalog-image-bad-gateway",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="org.springframework.web.client.HttpServerErrorException$BadGateway",
        message_template="502 Bad Gateway from GET https://images.example.com/products/{sku}.jpg",
        stack_frames=frames(
            "org.springframework.web.client.DefaultResponseErrorHandler.handleError:146",
            "com.example.catalog.image.ImageClient.fetch:51",
            "com.example.catalog.image.ThumbnailService.thumbnail:33",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.5,
        family_id="image-cdn",
    ),
    FailureType(
        id="catalog-image-premature-eof",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="java.io.IOException",
        message_template="Premature EOF while reading image for product {sku}",
        stack_frames=frames(
            "java.io.InputStream.readNBytes:409",
            "com.example.catalog.image.ImageClient.download:74",
            "com.example.catalog.image.ImageImportJob.importImages:58",
        ),
        base_rate_per_hour=0.2,
        family_id="image-cdn",
    ),
    FailureType(
        id="auth-jwt-signature-mismatch",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="io.jsonwebtoken.security.SignatureException",
        message_template=(
            "JWT signature does not match locally computed signature for key {key_id}"
        ),
        stack_frames=frames(
            "io.jsonwebtoken.impl.DefaultJwtParser.verifySignature:351",
            "com.example.auth.token.TokenVerifier.verify:45",
            "com.example.auth.token.TokenFilter.doFilterInternal:38",
            *SPRING_WEB,
        ),
        base_rate_per_hour=1.0,
        family_id="signing-key-rotation",
    ),
    FailureType(
        id="auth-unknown-key-id",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="com.example.auth.token.InvalidTokenException",
        message_template="Token for user {user_id} rejected: unknown key id {key_id}",
        stack_frames=frames(
            "com.example.auth.token.KeyResolver.resolve:29",
            "com.example.auth.token.RefreshService.refresh:66",
            "com.example.auth.token.RefreshController.refresh:31",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.4,
        family_id="signing-key-rotation",
    ),
    FailureType(
        id="auth-ldap-communication",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="javax.naming.CommunicationException",
        message_template="ldap-{node}.internal:636 [Root exception is java.net.ConnectException]",
        stack_frames=frames(
            "com.sun.jndi.ldap.LdapCtx.connect:2813",
            "com.example.auth.directory.DirectoryClient.lookup:57",
            "com.example.auth.login.LoginService.authenticate:82",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.3,
        family_id="directory-outage",
    ),
    FailureType(
        id="auth-ldap-spring-communication",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="org.springframework.ldap.CommunicationException",
        message_template=(
            "ldap-{node}.internal:636; nested exception is javax.naming.CommunicationException"
        ),
        stack_frames=frames(
            "org.springframework.ldap.support.LdapUtils.convertLdapException:108",
            "com.example.auth.directory.GroupSync.syncGroups:44",
            "com.example.auth.directory.GroupSyncJob.run:21",
        ),
        base_rate_per_hour=0.1,
        family_id="directory-outage",
    ),
    FailureType(
        id="auth-session-redis-failure",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="org.springframework.data.redis.RedisConnectionFailureException",
        message_template="Unable to connect to Redis at sessions-{node}.internal:6379",
        stack_frames=frames(
            "org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory"
            ".getConnection:1263",
            "com.example.auth.session.SessionRepository.findById:42",
            "com.example.auth.session.SessionFilter.doFilterInternal:35",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.6,
        family_id="session-store",
    ),
    FailureType(
        id="auth-session-load-failed",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="java.lang.IllegalStateException",
        message_template="Session {session_id} could not be loaded",
        stack_frames=frames(
            "com.example.auth.session.SessionService.load:58",
            "com.example.auth.session.SessionController.current:26",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.4,
        family_id="session-store",
    ),
    FailureType(
        id="checkout-staging-inventory-timeout",
        application=CHECKOUT,
        environment=STAGING,
        exception_type="feign.RetryableException",
        message_template="Read timed out executing GET http://inventory/api/stock/{sku}",
        stack_frames=frames(
            "feign.FeignException.errorExecuting:268",
            "com.example.checkout.inventory.InventoryClient.stock:18",
            "com.example.checkout.inventory.StockService.reserve:49",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.5,
        family_id="inventory-latency",
    ),
    FailureType(
        id="checkout-staging-stock-unavailable",
        application=CHECKOUT,
        environment=STAGING,
        exception_type="com.example.checkout.inventory.StockUnavailableException",
        message_template="Stock check for {sku} failed after {attempt} attempts",
        stack_frames=frames(
            "com.example.checkout.inventory.StockService.reserve:61",
            "com.example.checkout.order.OrderService.placeOrder:122",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.3,
        family_id="inventory-latency",
    ),
    FailureType(
        id="checkout-broker-connect",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="org.springframework.amqp.AmqpConnectException",
        message_template="java.net.ConnectException: Connection refused to rabbit-{node}.internal",
        stack_frames=frames(
            "org.springframework.amqp.rabbit.support.RabbitExceptionTranslator"
            ".convertRabbitAccessException:61",
            "org.springframework.amqp.rabbit.core.RabbitTemplate.convertAndSend:1124",
            "com.example.checkout.order.OrderEventPublisher.publishPlaced:33",
            "com.example.checkout.order.OrderService.placeOrder:140",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.4,
        family_id="message-broker",
    ),
    FailureType(
        id="catalog-broker-listener-failed",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type=("org.springframework.amqp.rabbit.support.ListenerExecutionFailedException"),
        message_template="Listener threw exception while handling price update for {sku}",
        stack_frames=frames(
            "com.example.catalog.price.PriceUpdateListener.onMessage:39",
            *SPRING_LISTENER,
        ),
        base_rate_per_hour=0.3,
        family_id="message-broker",
    ),
    FailureType(
        id="catalog-staging-missing-column-find",
        application=CATALOG,
        environment=STAGING,
        exception_type="org.postgresql.util.PSQLException",
        message_template='ERROR: column "discount_rate" does not exist (position {position})',
        stack_frames=frames(
            "org.postgresql.core.v3.QueryExecutorImpl.receiveErrorResponse:2725",
            "com.example.catalog.product.ProductRepository.findBySku:47",
            "com.example.catalog.product.ProductController.get:31",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.6,
        family_id="staging-migration",
    ),
    FailureType(
        id="catalog-staging-missing-column-search",
        application=CATALOG,
        environment=STAGING,
        exception_type="org.postgresql.util.PSQLException",
        message_template='ERROR: column "discount_rate" does not exist (position {position})',
        stack_frames=frames(
            "org.postgresql.core.v3.QueryExecutorImpl.receiveErrorResponse:2725",
            "com.example.catalog.product.ProductRepository.search:88",
            "com.example.catalog.search.SearchController.search:29",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.4,
        family_id="staging-migration",
    ),
    FailureType(
        id="catalog-staging-bad-sql-grammar",
        application=CATALOG,
        environment=STAGING,
        exception_type="org.springframework.jdbc.BadSqlGrammarException",
        message_template=(
            "PreparedStatementCallback; bad SQL grammar "
            "[select sku, name, discount_rate from product where category_id = ?]"
        ),
        stack_frames=frames(
            "org.springframework.jdbc.support.SQLStateSQLExceptionTranslator.doTranslate:103",
            "com.example.catalog.category.CategoryRepository.products:55",
            "com.example.catalog.category.CategoryController.products:36",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.2,
        family_id="staging-migration",
    ),
    FailureType(
        id="auth-staging-login-rate-limited",
        application=AUTH,
        environment=STAGING,
        exception_type="com.example.auth.ratelimit.RateLimitExceededException",
        message_template="Too many login attempts for user {user_id}",
        stack_frames=frames(
            "com.example.auth.ratelimit.RateLimiter.acquire:41",
            "com.example.auth.login.LoginController.login:37",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.8,
        family_id="login-rate-limit",
    ),
    FailureType(
        id="auth-staging-reset-rate-limited",
        application=AUTH,
        environment=STAGING,
        exception_type="com.example.auth.ratelimit.RateLimitExceededException",
        message_template="Too many password reset requests for user {user_id}",
        stack_frames=frames(
            "com.example.auth.ratelimit.RateLimiter.acquire:41",
            "com.example.auth.password.PasswordResetController.requestReset:45",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.2,
        family_id="login-rate-limit",
    ),
    FailureType(
        id="auth-staging-too-many-requests",
        application=AUTH,
        environment=STAGING,
        exception_type="org.springframework.web.server.ResponseStatusException",
        message_template='429 TOO_MANY_REQUESTS "Retry after {seconds} seconds"',
        stack_frames=frames(
            "com.example.auth.ratelimit.RateLimitInterceptor.preHandle:52",
            "org.springframework.web.servlet.HandlerExecutionChain.applyPreHandle:146",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.3,
        family_id="login-rate-limit",
    ),
)

SINGLETONS = (
    FailureType(
        id="checkout-empty-cart",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="java.lang.IllegalStateException",
        message_template="Cart is empty",
        stack_frames=frames(
            "com.example.checkout.cart.CartService.checkout:42",
            "com.example.checkout.cart.CartController.checkout:27",
            *SPRING_WEB,
        ),
        base_rate_per_hour=2.0,
    ),
    FailureType(
        id="checkout-invalid-coupon",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="java.lang.IllegalArgumentException",
        message_template="Invalid coupon code {coupon}",
        stack_frames=frames(
            "com.example.checkout.coupon.CouponService.apply:58",
            "com.example.checkout.cart.CartController.applyCoupon:49",
            *SPRING_WEB,
        ),
        base_rate_per_hour=1.5,
    ),
    FailureType(
        id="checkout-optimistic-lock",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="org.springframework.orm.ObjectOptimisticLockingFailureException",
        message_template=(
            "Row was updated or deleted by another transaction: [com.example.checkout.order"
            ".Order#{order_id}]"
        ),
        stack_frames=frames(
            "org.springframework.orm.jpa.vendor.HibernateJpaDialect"
            ".convertHibernateAccessException:325",
            "com.example.checkout.order.OrderService.updateStatus:164",
            "com.example.checkout.order.OrderStatusListener.onMessage:28",
            *SPRING_LISTENER,
        ),
        base_rate_per_hour=0.3,
    ),
    FailureType(
        id="checkout-address-null",
        application=CHECKOUT,
        environment=PRODUCTION,
        exception_type="java.lang.NullPointerException",
        message_template=(
            'Cannot invoke "com.example.checkout.address.Address.country()" because '
            '"order.shippingAddress" is null'
        ),
        stack_frames=frames(
            "com.example.checkout.shipping.ShippingCalculator.cost:36",
            "com.example.checkout.order.OrderService.quote:91",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.6,
    ),
    FailureType(
        id="checkout-staging-unreadable-body",
        application=CHECKOUT,
        environment=STAGING,
        exception_type="org.springframework.http.converter.HttpMessageNotReadableException",
        message_template="JSON parse error: Unexpected character at line 1, column {position}",
        stack_frames=frames(
            "org.springframework.http.converter.json.AbstractJackson2HttpMessageConverter"
            ".readJavaType:406",
            "org.springframework.web.servlet.mvc.method.annotation"
            ".RequestResponseBodyMethodProcessor.readWithMessageConverters:202",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.4,
    ),
    FailureType(
        id="catalog-price-number-format",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="java.lang.NumberFormatException",
        message_template='For input string: "{count},99"',
        stack_frames=frames(
            "java.lang.NumberFormatException.forInputString:67",
            "java.lang.Double.parseDouble:792",
            "com.example.catalog.importer.PriceFeedParser.parseLine:61",
            "com.example.catalog.importer.PriceFeedJob.run:34",
        ),
        base_rate_per_hour=0.5,
    ),
    FailureType(
        id="catalog-product-not-found",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="com.example.catalog.product.ProductNotFoundException",
        message_template="Product {sku} not found",
        stack_frames=frames(
            "com.example.catalog.product.ProductService.findBySku:61",
            "com.example.catalog.product.ProductController.get:31",
            *SPRING_WEB,
        ),
        base_rate_per_hour=3.0,
    ),
    FailureType(
        id="catalog-concurrent-modification",
        application=CATALOG,
        environment=PRODUCTION,
        exception_type="java.util.ConcurrentModificationException",
        message_template="Concurrent modification of category tree",
        stack_frames=frames(
            "java.util.ArrayList$Itr.checkForComodification:1095",
            "com.example.catalog.category.CategoryTree.flatten:73",
            "com.example.catalog.category.CategoryController.tree:24",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.2,
    ),
    FailureType(
        id="catalog-staging-date-parse",
        application=CATALOG,
        environment=STAGING,
        exception_type="java.time.format.DateTimeParseException",
        message_template="Text '{count}/10/2026' could not be parsed at index 0",
        stack_frames=frames(
            "java.time.format.DateTimeFormatter.parseResolved0:2108",
            "com.example.catalog.promotion.PromotionImporter.parseDate:52",
            "com.example.catalog.promotion.PromotionImportJob.run:30",
        ),
        base_rate_per_hour=0.3,
    ),
    FailureType(
        id="catalog-staging-class-cast",
        application=CATALOG,
        environment=STAGING,
        exception_type="java.lang.ClassCastException",
        message_template=(
            "class java.lang.Integer cannot be cast to class java.lang.String "
            "(attribute {attribute})"
        ),
        stack_frames=frames(
            "com.example.catalog.product.AttributeMapper.toText:44",
            "com.example.catalog.product.ProductController.get:35",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.2,
    ),
    FailureType(
        id="auth-bad-credentials",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="org.springframework.security.authentication.BadCredentialsException",
        message_template="Bad credentials",
        stack_frames=frames(
            "org.springframework.security.authentication.dao.DaoAuthenticationProvider"
            ".additionalAuthenticationChecks:89",
            "com.example.auth.login.LoginService.authenticate:76",
            "com.example.auth.login.LoginController.login:37",
            *SPRING_WEB,
        ),
        base_rate_per_hour=2.5,
    ),
    FailureType(
        id="auth-access-denied",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="org.springframework.security.access.AccessDeniedException",
        message_template="Access Denied for user {user_id}",
        stack_frames=frames(
            "org.springframework.security.authorization.method"
            ".AuthorizationManagerBeforeMethodInterceptor.attemptAuthorization:257",
            "com.example.auth.admin.UserAdminController.disable:48",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.4,
    ),
    FailureType(
        id="auth-mail-timeout",
        application=AUTH,
        environment=PRODUCTION,
        exception_type="java.util.concurrent.TimeoutException",
        message_template="Sending password reset mail timed out after {seconds} seconds",
        stack_frames=frames(
            "java.util.concurrent.CompletableFuture.timedGet:1960",
            "com.example.auth.password.ResetMailSender.send:39",
            "com.example.auth.password.PasswordResetService.requestReset:57",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.2,
    ),
    FailureType(
        id="auth-staging-unsupported-grant",
        application=AUTH,
        environment=STAGING,
        exception_type="java.lang.UnsupportedOperationException",
        message_template="Grant type device_code is not supported",
        stack_frames=frames(
            "com.example.auth.token.GrantHandlers.handlerFor:31",
            "com.example.auth.token.TokenController.token:42",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.3,
    ),
    FailureType(
        id="auth-staging-index-out-of-bounds",
        application=AUTH,
        environment=STAGING,
        exception_type="java.lang.ArrayIndexOutOfBoundsException",
        message_template="Index {count} out of bounds for length 2",
        stack_frames=frames(
            "com.example.auth.login.UsernameParser.domainOf:22",
            "com.example.auth.login.LoginService.authenticate:71",
            "com.example.auth.login.LoginController.login:37",
            *SPRING_WEB,
        ),
        base_rate_per_hour=0.2,
    ),
)

FAILURE_TYPES = FAMILY_MEMBERS + SINGLETONS


def default_catalogue(seed: int, start: datetime, end: datetime) -> Catalogue:
    rng = np.random.default_rng(seed)
    incidents = draw_incidents(rng, FAILURE_TYPES, start, end)
    return Catalogue(families=FAMILIES, failure_types=FAILURE_TYPES, incidents=incidents)


def hour_starts(start: datetime, end: datetime) -> list[datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if not (is_whole_hour(start) and is_whole_hour(end)):
        raise ValueError("start and end must be whole hours")
    if end <= start:
        raise ValueError("end must be after start")
    hour_count = int((end - start) / timedelta(hours=1))
    return [start + timedelta(hours=offset) for offset in range(hour_count)]


def is_whole_hour(moment: datetime) -> bool:
    return (moment.minute, moment.second, moment.microsecond) == (0, 0, 0)


def draw_incidents(
    rng: np.random.Generator,
    failure_types: tuple[FailureType, ...],
    start: datetime,
    end: datetime,
) -> tuple[Incident, ...]:
    hours = hour_starts(start, end)
    slot_hours = (len(hours) - WARM_UP_HOURS) // INCIDENT_COUNT
    if slot_hours < MAX_INCIDENT_HOURS:
        raise ValueError(f"the simulated period must be at least {MINIMUM_DAYS} days long")
    hard_slots = {
        int(slot) for slot in rng.choice(INCIDENT_COUNT, HARD_INCIDENT_COUNT, replace=False)
    }
    incidents = []
    for slot in range(INCIDENT_COUNT):
        hard = slot in hard_slots
        max_hours = MAX_HARD_INCIDENT_HOURS if hard else MAX_INCIDENT_HOURS
        max_multiplier = MAX_HARD_MULTIPLIER if hard else MAX_MULTIPLIER
        duration = int(rng.integers(1, max_hours + 1))
        offset = WARM_UP_HOURS + slot * slot_hours + int(rng.integers(0, slot_hours - duration + 1))
        target = failure_types[int(rng.integers(len(failure_types)))]
        incident_start = hours[offset]
        incidents.append(
            Incident(
                id=f"incident-{slot + 1:02d}",
                application=target.application,
                environment=target.environment,
                failure_ids=affected_failure_ids(target, failure_types),
                start=incident_start,
                end=incident_start + timedelta(hours=duration),
                multiplier=round(float(rng.uniform(MIN_MULTIPLIER, max_multiplier)), 1),
            )
        )
    return tuple(incidents)


def affected_failure_ids(
    target: FailureType, failure_types: tuple[FailureType, ...]
) -> tuple[str, ...]:
    if target.family_id is None:
        return (target.id,)
    return tuple(
        failure.id
        for failure in failure_types
        if failure.family_id == target.family_id
        and failure.application == target.application
        and failure.environment == target.environment
    )
