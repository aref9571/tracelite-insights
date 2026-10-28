import hashlib
import re

MAX_FRAMES = 30

JAVA_FRAME = re.compile(r"^\s*at\s+(?:[^\s/(]*/)*([^\s/(]+)\(.*\)\s*$")
NESTED_THROWABLE_PREFIXES = ("Caused by:", "Suppressed:")


def fingerprint(exception_type: str, stack_trace: str | None) -> str:
    digest = hashlib.sha256()
    for part in [exception_type.strip(), *normalised_frames(stack_trace)]:
        digest.update(f"{len(part)}:{part}\n".encode())
    return digest.hexdigest()


def normalised_frames(stack_trace: str | None) -> list[str]:
    frames: list[str] = []
    for line in (stack_trace or "").splitlines():
        if line.strip().startswith(NESTED_THROWABLE_PREFIXES):
            break
        match = JAVA_FRAME.match(line)
        if match is None:
            continue
        frame = match.group(1)
        if not frames or frames[-1] != frame:
            frames.append(frame)
        if len(frames) == MAX_FRAMES:
            break
    return frames
