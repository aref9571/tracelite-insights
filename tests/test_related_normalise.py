from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from insights.domain import Event, Issue, IssueStatus
from insights.related.normalise import (
    FRAME_LIMIT,
    application_frames,
    issue_document,
    split_words,
    template_message,
)

MOMENT = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Order 42 not found", "Order <num> not found"),
        ("Price 19.99 rejected", "Price <num> rejected"),
        ("HikariPool-1 is closed", "HikariPool-<num> is closed"),
        ("Product SKU-12345 not found", "Product SKU-<num> not found"),
        ("Request 3fa85f64-5717-4562-b3fc-2c963f66afa6 failed", "Request <uuid> failed"),
        ("Request 3FA85F64-5717-4562-B3FC-2C963F66AFA6 failed", "Request <uuid> failed"),
        ("Session 0a1b2c3d4e5f could not be loaded", "Session <hex> could not be loaded"),
        ("Payment pay_00ab12cd34 failed", "Payment pay_<hex> failed"),
        ("Bad pointer 0x7ffe12", "Bad pointer <hex>"),
        ("Word deadbeefcafe stays", "Word deadbeefcafe stays"),
        ("Host 10.0.4.17 unreachable", "Host <ip> unreachable"),
        ("Connection refused: 10.0.4.17:9200", "Connection refused: <ip>"),
        ("Connection to db-3.internal:5432 refused", "Connection to <host> refused"),
        ("Cannot reach localhost:8080", "Cannot reach <host>"),
        ("Timed out after 250 ms", "Timed out after <duration>"),
        ("Timed out after 30000ms.", "Timed out after <duration>."),
        ("Retry in 5 seconds", "Retry in <duration>"),
        ("Took 1.5s", "Took <duration>"),
        ("Command timed out after 500 millisecond(s)", "Command timed out after <duration>(s)"),
        ("Waited 2 hours", "Waited <duration>"),
        ('Column "discount_rate" does not exist', "Column <str> does not exist"),
        ("Text '12/10/2026' could not be parsed", "Text <str> could not be parsed"),
        ("Can't parse 12 items", "Can't parse <num> items"),
        ("Cart is empty", "Cart is empty"),
        ("", ""),
    ],
)
def test_template_message_replaces_variable_parts_with_tokens(message, expected):
    assert template_message(message) == expected


def test_template_message_makes_messages_of_one_failure_identical():
    first = template_message("Connection to db-1.internal:5432 refused after 812 ms")
    second = template_message("Connection to db-6.internal:5432 refused after 29001 ms")

    assert first == second


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("\tat com.example.cart.Cart.checkout(Cart.java:42)", ["Cart.checkout"]),
        ("\tat com.example.cart.Cart.checkout(Cart.java)", ["Cart.checkout"]),
        ("\tat com.example.cart.Cart.checkout(Unknown Source)", ["Cart.checkout"]),
        ("\tat com.example.io.NativeReader.read0(Native Method)", ["NativeReader.read0"]),
        ("\tat app//com.example.cart.Cart.checkout(Cart.java:42)", ["Cart.checkout"]),
        ("\tat com.example.cart.Cart$Line.total(Cart.java:88)", ["Cart$Line.total"]),
        (
            "    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:181)",
            ["HikariPool.getConnection"],
        ),
        ("\tat java.base/java.lang.Thread.run(Thread.java:1583)", []),
        ("\tat java.util.ArrayList$Itr.next(ArrayList.java:1052)", []),
        ("\tat jdk.internal.reflect.Method.invoke(Method.java:580)", []),
        ("\tat sun.nio.ch.Net.pollConnect(Native Method)", []),
        ("\tat org.springframework.web.servlet.FrameworkServlet.service(F.java:897)", []),
        ("\tat org.hibernate.Session.flush(Session.java:12)", []),
        ("java.lang.IllegalStateException: Cart 42 is empty", []),
        ("\t... 12 more", []),
        ("\tat com.example.cart.Cart.audit(Cart.ja", []),
    ],
)
def test_application_frames_parse_one_line(line, expected):
    assert application_frames(line) == expected


def test_application_frames_keep_the_order_of_the_trace():
    trace = (
        "org.postgresql.util.PSQLException: Connection refused\n"
        "\tat org.postgresql.core.v3.ConnectionFactoryImpl.openConnectionImpl(C.java:346)\n"
        "\tat org.springframework.jdbc.datasource.DataSourceUtils.getConnection(D.java:84)\n"
        "\tat com.example.checkout.order.OrderRepository.save(OrderRepository.java:64)\n"
        "\tat jakarta.servlet.http.HttpServlet.service(HttpServlet.java:658)\n"
    )

    assert application_frames(trace) == [
        "ConnectionFactoryImpl.openConnectionImpl",
        "OrderRepository.save",
        "HttpServlet.service",
    ]


def test_application_frames_keep_at_most_the_frame_limit():
    trace = "\n".join(
        f"\tat com.example.Step{number}.run(Step{number}.java:1)" for number in range(20)
    )

    frames = application_frames(trace)

    assert len(frames) == FRAME_LIMIT
    assert frames[-1] == f"Step{FRAME_LIMIT - 1}.run"


def test_application_frames_count_the_limit_after_dropping_framework_frames():
    lines = []
    for number in range(10):
        lines.append(f"\tat java.lang.reflect.Step{number}.invoke(Step.java:1)")
        lines.append(f"\tat com.example.Step{number}.run(Step{number}.java:1)")

    frames = application_frames("\n".join(lines), limit=3)

    assert frames == ["Step0.run", "Step1.run", "Step2.run"]


def test_application_frames_collapse_consecutive_repeats():
    trace = (
        "\tat com.example.Tree.depth(Tree.java:10)\n"
        "\tat com.example.Tree.depth(Tree.java:10)\n"
        "\tat com.example.Main.run(Main.java:5)\n"
    )

    assert application_frames(trace) == ["Tree.depth", "Main.run"]


def test_application_frames_stop_at_the_first_nested_exception():
    trace = (
        "\tat com.example.cart.Cart.checkout(Cart.java:42)\n"
        "Caused by: java.io.IOException: closed\n"
        "\tat com.example.io.Pipe.read(Pipe.java:9)\n"
    )

    assert application_frames(trace) == ["Cart.checkout"]


@pytest.mark.parametrize("trace", [None, "", "no frames here"])
def test_application_frames_of_a_trace_without_frames_are_empty(trace):
    assert application_frames(trace) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PSQLException", ["psql", "exception"]),
        ("SQLTransientConnectionException", ["sql", "transient", "connection", "exception"]),
        ("org.postgresql.util.PSQLException", ["org", "postgresql", "util", "psql", "exception"]),
        ("OrderRepository.save", ["order", "repository", "save"]),
        ("HikariPool.getConnection", ["hikari", "pool", "get", "connection"]),
        ("InputStream.readNBytes", ["input", "stream", "read", "n", "bytes"]),
        ("ArrayList$Itr.next", ["array", "list", "itr", "next"]),
        ("TOO_MANY_REQUESTS", ["too", "many", "requests"]),
        ("Http2Client", ["http", "2", "client"]),
        ("Connection to <host> refused", ["connection", "to", "<host>", "refused"]),
        ("timed out after <duration>.", ["timed", "out", "after", "<duration>"]),
        ("", []),
    ],
)
def test_split_words_splits_camel_case_dots_and_punctuation(text, expected):
    assert split_words(text) == expected


def make_issue(exception_type="org.postgresql.util.PSQLException", message=None):
    return Issue(
        id=UUID(int=1),
        application="checkout-service",
        environment="production",
        fingerprint="f" * 64,
        exception_type=exception_type,
        message=message,
        status=IssueStatus.UNRESOLVED,
        occurrence_count=3,
        first_seen_at=MOMENT,
        last_seen_at=MOMENT,
    )


def make_event(message, stack_trace):
    return Event(
        id=uuid4(),
        exception_type="org.postgresql.util.PSQLException",
        message=message,
        stack_trace=stack_trace,
        occurred_at=MOMENT,
        received_at=MOMENT,
    )


TRACE = (
    "org.postgresql.util.PSQLException: Connection refused\n"
    "\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:181)\n"
    "\tat com.example.checkout.order.OrderRepository.save(OrderRepository.java:64)\n"
    "\tat org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:897)\n"
)


def test_issue_document_combines_type_message_and_frame_words():
    issue = make_issue(message="Connection to db-2.internal:5432 refused after 812 ms")
    events = [make_event("Connection to db-5.internal:5432 refused after 90 ms", TRACE)]

    document = issue_document(issue, events)

    assert document == (
        "org postgresql util psql exception "
        "connection to <host> refused after <duration> "
        "hikari pool get connection order repository save"
    )


def test_issue_document_uses_each_template_and_frame_once():
    issue = make_issue(message="Order 1 failed")
    other_trace = TRACE.replace("OrderRepository.save", "OrderRepository.load")
    events = [
        make_event("Order 2 failed", TRACE),
        make_event("Order 3 failed", TRACE),
        make_event("Order 4 failed", other_trace),
    ]

    document = issue_document(issue, events)

    assert document == (
        "org postgresql util psql exception order <num> failed "
        "hikari pool get connection order repository save order repository load"
    )


def test_issue_document_without_message_or_events_has_only_the_type_words():
    assert issue_document(make_issue(), []) == "org postgresql util psql exception"


def test_issue_document_skips_events_without_message_or_trace():
    events = [make_event(None, None)]

    assert issue_document(make_issue(message="Cart is empty"), events) == (
        "org postgresql util psql exception cart is empty"
    )
