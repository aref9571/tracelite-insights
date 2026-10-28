from insights.fingerprint import MAX_FRAMES, fingerprint, normalised_frames

TYPE = "java.lang.IllegalStateException"
TRACE = (
    "java.lang.IllegalStateException: Cart 42 is empty\n"
    "\tat com.example.cart.Cart.checkout(Cart.java:42)\n"
    "\tat com.example.cart.CartController.checkout(CartController.java:18)\n"
    "\tat java.base/java.lang.Thread.run(Thread.java:1583)\n"
)


def test_fingerprint_matches_the_fixed_hash_from_tracelite():
    assert fingerprint(TYPE, TRACE) == (
        "02e602e2d13a3cd7f7c8133aa8968cb9302d567b6dbcaaa63525a909bf307659"
    )


def test_fingerprint_ignores_message_line_numbers_and_file_names():
    changed = (
        "java.lang.IllegalStateException: Cart 97 is empty\n"
        "\tat com.example.cart.Cart.checkout(Cart.kt:57)\n"
        "\tat com.example.cart.CartController.checkout(Unknown Source)\n"
        "\tat java.base/java.lang.Thread.run(Thread.java:1595)\n"
    )

    assert fingerprint(TYPE, changed) == fingerprint(TYPE, TRACE)


def test_fingerprint_ignores_module_and_class_loader_prefixes():
    without_module = TRACE.replace("java.base/", "")
    with_class_loader = TRACE.replace("at com.example", "at app//com.example")

    assert fingerprint(TYPE, without_module) == fingerprint(TYPE, TRACE)
    assert fingerprint(TYPE, with_class_loader) == fingerprint(TYPE, TRACE)


def test_fingerprint_ignores_surrounding_whitespace_and_windows_line_endings():
    assert fingerprint(f"  {TYPE}\n", TRACE.replace("\n", "\r\n")) == fingerprint(TYPE, TRACE)


def test_fingerprint_changes_with_exception_type_or_call_path():
    other_path = TRACE.replace("CartController.checkout", "CartController.pay")

    assert fingerprint("java.lang.RuntimeException", TRACE) != fingerprint(TYPE, TRACE)
    assert fingerprint(TYPE, other_path) != fingerprint(TYPE, TRACE)


def test_fingerprint_without_frames_uses_the_exception_type_only():
    type_only = fingerprint(TYPE, None)

    assert fingerprint(TYPE, "") == type_only
    assert fingerprint(TYPE, "Traceback (most recent call last):\n  File x.py") == type_only
    assert fingerprint("java.lang.RuntimeException", None) != type_only


def test_normalised_frames_keep_only_class_and_method():
    assert normalised_frames(TRACE) == [
        "com.example.cart.Cart.checkout",
        "com.example.cart.CartController.checkout",
        "java.lang.Thread.run",
    ]


def test_normalised_frames_stop_at_the_first_nested_exception():
    caused = (
        TRACE
        + "Caused by: java.io.IOException: closed\n\tat com.example.io.Pipe.read(Pipe.java:9)\n"
    )
    suppressed = (
        TRACE + "\tSuppressed: java.io.IOException\n\t\tat com.example.io.Pipe.close(Pipe.java:3)\n"
    )

    assert normalised_frames(caused) == normalised_frames(TRACE)
    assert normalised_frames(suppressed) == normalised_frames(TRACE)


def test_normalised_frames_collapse_consecutive_repeats():
    recursive = (
        "java.lang.StackOverflowError\n"
        "\tat com.example.Tree.depth(Tree.java:10)\n"
        "\tat com.example.Tree.depth(Tree.java:10)\n"
        "\tat com.example.Tree.depth(Tree.java:10)\n"
        "\tat com.example.Main.run(Main.java:5)\n"
    )

    assert normalised_frames(recursive) == ["com.example.Tree.depth", "com.example.Main.run"]


def test_normalised_frames_use_at_most_the_frame_limit():
    lines = [f"\tat com.example.Step{number}.run(Step{number}.java:1)" for number in range(40)]

    frames = normalised_frames("\n".join(lines))

    assert len(frames) == MAX_FRAMES
    assert frames[-1] == f"com.example.Step{MAX_FRAMES - 1}.run"


def test_normalised_frames_ignore_a_truncated_last_line():
    truncated = TRACE + "\tat com.example.cart.Cart.audit(Cart.ja"

    assert normalised_frames(truncated) == normalised_frames(TRACE)
