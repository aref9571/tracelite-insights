import re
from collections.abc import Iterable, Sequence

from insights.domain import Event, Issue
from insights.fingerprint import JAVA_FRAME, NESTED_THROWABLE_PREFIXES

FRAME_LIMIT = 8
FRAMEWORK_PREFIXES = ("java.", "jdk.", "sun.", "org.springframework.", "org.hibernate.")

HEX_DIGIT = "[0-9a-fA-F]"
DURATION_UNITS = r"ms|milliseconds?|secs?|seconds?|s|mins?|minutes?|h|hours?"

MESSAGE_RULES = (
    (re.compile(r"(?<!\w)\"[^\"]*\"(?!\w)|(?<!\w)'[^']*'(?!\w)"), "<str>"),
    (
        re.compile(
            rf"(?<![0-9A-Za-z]){HEX_DIGIT}{{8}}-{HEX_DIGIT}{{4}}-{HEX_DIGIT}{{4}}"
            rf"-{HEX_DIGIT}{{4}}-{HEX_DIGIT}{{12}}(?![0-9A-Za-z])"
        ),
        "<uuid>",
    ),
    (re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?(?![\d.])"), "<ip>"),
    (re.compile(r"\b[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*:\d{1,5}\b"), "<host>"),
    (
        re.compile(
            rf"(?<![0-9A-Za-z])(?:0x{HEX_DIGIT}+|(?={HEX_DIGIT}*\d)(?={HEX_DIGIT}*[a-fA-F])"
            rf"{HEX_DIGIT}{{8,}})(?![0-9A-Za-z])"
        ),
        "<hex>",
    ),
    (re.compile(rf"\b\d+(?:\.\d+)?\s?(?:{DURATION_UNITS})\b"), "<duration>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<num>"),
)

WORD = re.compile(r"<[a-z]+>|[A-Za-z0-9]+")
WORD_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def template_message(message: str) -> str:
    for pattern, token in MESSAGE_RULES:
        message = pattern.sub(token, message)
    return message


def application_frames(stack_trace: str | None, limit: int = FRAME_LIMIT) -> list[str]:
    frames: list[str] = []
    for line in (stack_trace or "").splitlines():
        if len(frames) == limit or line.strip().startswith(NESTED_THROWABLE_PREFIXES):
            break
        match = JAVA_FRAME.match(line)
        if match is None or match.group(1).startswith(FRAMEWORK_PREFIXES):
            continue
        frame = class_and_method(match.group(1))
        if not frames or frames[-1] != frame:
            frames.append(frame)
    return frames


def class_and_method(qualified_method: str) -> str:
    return ".".join(qualified_method.split(".")[-2:])


def split_words(text: str) -> list[str]:
    words: list[str] = []
    for token in WORD.findall(text):
        if token.startswith("<"):
            words.append(token)
        else:
            words.extend(part.lower() for part in WORD_PART.findall(token))
    return words


def issue_document(issue: Issue, sample_events: Sequence[Event]) -> str:
    messages = [issue.message, *(event.message for event in sample_events)]
    templates = unique(template_message(message) for message in messages if message)
    frames = unique(
        frame for event in sample_events for frame in application_frames(event.stack_trace)
    )
    words = split_words(issue.exception_type)
    for text in [*templates, *frames]:
        words.extend(split_words(text))
    return " ".join(words)


def unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
