"""Run workspace tools inside a Sandbox Environment through a Sandbox Adapter.

The adapter runs commands and reads files in the sandbox. Workspace file and
shell tools run there through the helper module, executed by the sandbox's own
interpreter after a digest check, so the same code produces the same results
locally and in the sandbox.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
import hashlib
import importlib.resources
import os
from pathlib import Path
import shlex
from typing import Annotated, Any, Literal, Protocol
import zlib

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from mistralai_vibe_local_harness.vibe import _sandbox_helper

HELPER_MISSING_EXIT_CODE = 87
"""Exit code of the bootstrap when no digest-matching helper copy exists."""

HELPER_CRASH_LIMIT = 3
"""Tool helper crashes in a row after which the sandbox counts as failed."""

# Helper exits that mean it never started, so no command ran: the shell could
# not run the interpreter (126, 127), or no helper copy was found even after
# installing one.
_HELPER_NOT_STARTED_EXIT_CODES = frozenset({126, 127, HELPER_MISSING_EXIT_CODE})

_HELPER_RESOURCE = "_sandbox_helper.py"
_HELPER_CACHE_DIRNAME = "mistralai-vibe-sandbox-helper"
_MATERIALIZE_TIMEOUT_SECONDS = 60.0
_UPLOAD_TIMEOUT_SECONDS = 60.0
# Where helper commands run. A tool's working directory travels in its
# request, so a model that deletes or moves it gets a tool error from the
# helper, as on the host, instead of a helper that cannot start.
_HELPER_CWD = "/"

REQUEST_CHUNK_BYTES = 64 * 1024
"""The largest request carried in one command line argument.

Linux caps a single argument at 128 KiB, and argv is visible to every process in
the sandbox; a larger request is streamed into a private file in chunks of
this size instead."""

# Finds a helper copy whose digest matches and runs it as a script. It prefers
# a copy installed with this package (located without importing the package),
# then one materialized earlier into the sandbox's temporary directory.
_BOOTSTRAP = f"""
import hashlib, importlib.util, os, sys, tempfile, types
digest = sys.argv[1]
def trusted(path):
    try:
        with open(path, "rb") as handle:
            source = handle.read()
    except OSError:
        return None
    normalized = source.replace(b"\\r\\n", b"\\n")
    return source if hashlib.sha256(normalized).hexdigest() == digest else None
candidates = []
try:
    spec = importlib.util.find_spec("mistralai_vibe_local_harness")
except (ImportError, ValueError):
    spec = None
for location in (spec and spec.submodule_search_locations) or ():
    candidates.append(os.path.join(location, "vibe", {_HELPER_RESOURCE!r}))
candidates.append(
    os.path.join(tempfile.gettempdir(), {_HELPER_CACHE_DIRNAME!r}, digest + ".py")
)
for path in candidates:
    source = trusted(path)
    if source is None:
        continue
    module = types.ModuleType("_vibe_sandbox_helper")
    module.__file__ = path
    sys.modules[module.__name__] = module
    exec(compile(source, path, "exec"), module.__dict__)
    sys.exit(module.main(sys.argv[2:]))
sys.exit({HELPER_MISSING_EXIT_CODE})
"""

# Writes the helper source carried in argv into the sandbox's temporary
# directory, atomically and only when its digest matches.
_MATERIALIZE = f"""
import base64, hashlib, os, sys, tempfile, zlib
digest = sys.argv[1]
source = zlib.decompress(base64.urlsafe_b64decode(sys.argv[2].encode("ascii")))
if hashlib.sha256(source.replace(b"\\r\\n", b"\\n")).hexdigest() != digest:
    sys.exit("sandbox helper digest mismatch")
directory = os.path.join(tempfile.gettempdir(), {_HELPER_CACHE_DIRNAME!r})
os.makedirs(directory, exist_ok=True)
descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
with os.fdopen(descriptor, "wb") as handle:
    handle.write(source)
os.replace(temporary, os.path.join(directory, digest + ".py"))
"""


# Creates a file only the sandbox user can read, holding the first chunk of a
# request, and prints its path.
_CREATE_REQUEST_FILE = """
import os, sys, tempfile
descriptor, path = tempfile.mkstemp(prefix="mistralai-vibe-request-")
with os.fdopen(descriptor, "w", encoding="ascii") as handle:
    handle.write(sys.argv[1])
sys.stdout.write(path)
"""

# Appends the next chunk of a request to its file.
_APPEND_REQUEST_CHUNK = """
import sys
with open(sys.argv[1], "a", encoding="ascii") as handle:
    handle.write(sys.argv[2])
"""


@dataclass(frozen=True, slots=True)
class SandboxExecResult:
    """The outcome of one command run in a Sandbox Environment."""

    exit_code: int
    stdout: str
    stderr: str


class SandboxAdapter(Protocol):
    """A Sandbox Environment the workspace tools run in instead of the host.

    Vibe's ``--agent-socket`` client is the implementation: this is the
    interface between Vibe and this runtime, not an extension point for other
    hosts, and it may change with Vibe.

    Paths are sandbox paths. ``workspace`` is the working directory a Session
    starts in, and ``python`` is the interpreter that runs the tool helper.

    ``execute`` runs ``command``, a POSIX ``sh`` command line whose arguments
    are quoted with :func:`shlex.join`, in the sandbox directory ``cwd`` and with
    the sandbox's own environment: nothing from the host's environment is
    passed in. A non-zero exit is a result, not an error. ``read_file`` returns
    at most ``max_bytes`` of a regular file, or None when there is no such file.

    ``execute`` raises :class:`SandboxCommandTimeoutError` when the sandbox
    stopped ``command`` because it outlived its time limit: ``timeout``, or a
    shorter limit of the sandbox's own, which the error names when the sandbox
    says. The model reads that as the command's timeout, as it would on the
    host, and the Session goes on.

    Otherwise both methods raise only when the sandbox itself fails, and the
    Session then fails the turn instead of handing the model a tool error:

    - :class:`SandboxUnavailableError`, or any ``OSError`` such as a
      ``ConnectionError``, when the sandbox is gone or cannot be reached;
    - any other ``TimeoutError`` when the adapter gave up waiting for the
      sandbox's answer.

    Any other exception is a bug in the adapter and propagates as is.

    ``execute`` must accept concurrent calls: background processes poll their
    process server while the Session's other tools, and other operations on
    those processes, run their own commands.
    """

    @property
    def workspace(self) -> str: ...

    @property
    def python(self) -> str: ...

    async def execute(
        self, command: str, cwd: str, timeout: float | None
    ) -> SandboxExecResult: ...

    async def read_file(self, path: str, max_bytes: int) -> bytes | None: ...


SANDBOX_UNAVAILABLE_CODE = "sandbox_unavailable"
"""The error code of a turn failed because its sandbox failed."""


class SandboxUnavailableError(Exception):
    """The Sandbox Environment can no longer run the Session's tools."""


class SandboxCommandTimeoutError(TimeoutError):
    """The sandbox stopped a command that outlived its time limit.

    The sandbox works: only the command failed. It is a ``TimeoutError``, so
    code that treats every timeout alike still does. ``timeout`` is the limit,
    in seconds, that the sandbox applied, when it said: it may be shorter than
    the one the command was run with.
    """

    def __init__(self, message: str, *, timeout: float | None = None) -> None:
        super().__init__(message)
        self.timeout = timeout


SANDBOX_FAILURES: tuple[type[Exception], ...] = (
    SandboxUnavailableError,
    OSError,
    TimeoutError,
)
"""What a Sandbox Adapter raises when the sandbox itself fails."""


class SandboxToolError(ValueError):
    """A tool failure the helper reported, or a response it could not give."""


class SandboxToolTimeoutError(SandboxToolError):
    """The sandbox stopped a helper command that outlived its time limit.

    ``timeout`` is the limit, in seconds, when the sandbox said what it was.
    """

    def __init__(self, timeout: float | None) -> None:
        super().__init__(
            "The sandbox stopped the command at its time limit"
            if timeout is None
            else f"The sandbox stopped the command after {timeout:g}s"
        )
        self.timeout = timeout


class SandboxHelperCrashedError(SandboxToolError):
    """The tool helper exited without answering, part way through a tool call.

    The command may or may not have run, and the model is told so.
    """


class HelperCrashCount:
    """How many times in a row the tool helper crashed during a tool call.

    One count per sandbox, shared by the Sessions whose tools run there. A
    crash during a tool call is a tool failure the model reads; the
    :data:`HELPER_CRASH_LIMIT`-th in a row fails the turn instead, as a sandbox
    that cannot run tools. Any helper run that answers resets the count.
    """

    __slots__ = ("_count",)

    def __init__(self) -> None:
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def answered(self) -> None:
        self._count = 0

    def crashed(self, failure: str) -> SandboxHelperCrashedError:
        """Count a crash: the error to raise for it.

        Raises :class:`SandboxUnavailableError` on the last crash allowed.
        """
        self._count += 1
        if self._count >= HELPER_CRASH_LIMIT:
            raise SandboxUnavailableError(
                f"The tool helper crashed {self._count} times in a row; "
                f"the last time: {failure}"
            )
        return SandboxHelperCrashedError(
            f"The tool helper crashed before it answered, so the command may "
            f"or may not have run: {failure}"
        )


class _HelperSucceeded(BaseModel):
    ok: Literal[True]
    result: JsonValue


class _HelperFailed(BaseModel):
    ok: Literal[False]
    error: str


_HELPER_RESPONSE: TypeAdapter[_HelperSucceeded | _HelperFailed] = TypeAdapter(
    Annotated[_HelperSucceeded | _HelperFailed, Field(discriminator="ok")]
)


def sandbox_path(
    path: str | os.PathLike[str], *, cwd: str | os.PathLike[str] | None = None
) -> Path:
    """A path in a Sandbox Environment, normalized as written.

    A relative path is joined onto ``cwd``, and ``.`` and ``..`` are collapsed
    lexically: this host's file system may not hold the path, or may link it
    elsewhere, so the path is never resolved against it. A path starting with
    ``~`` is kept as written, for the sandbox to expand against its own home.
    """
    raw = os.fspath(path)
    if raw.startswith("~"):
        return Path(raw)
    if cwd is not None:
        raw = os.path.join(cwd, raw)
    return Path(os.path.normpath(raw))


@cache
def helper_source() -> bytes:
    """The helper's source, as shipped with this package."""
    return (
        importlib.resources
        .files("mistralai_vibe_local_harness.vibe")
        .joinpath(_HELPER_RESOURCE)
        .read_bytes()
    )


@cache
def helper_digest() -> str:
    """The SHA-256 a helper copy must match, with line endings normalized."""
    return hashlib.sha256(helper_source().replace(b"\r\n", b"\n")).hexdigest()


type RequestOption = Literal["--request-base64", "--request-file"]


def helper_command(
    python: str, operation: str, option: RequestOption, argument: str
) -> str:
    """The command line that runs one helper operation in the sandbox.

    ``argument`` is the encoded request, or the sandbox path of the file it was
    streamed into.
    """
    return shlex.join([
        python,
        "-I",
        "-c",
        _BOOTSTRAP,
        helper_digest(),
        operation,
        option,
        argument,
    ])


def materialize_command(python: str) -> str:
    """The command line that installs the helper in the sandbox's temp dir."""
    payload = base64.urlsafe_b64encode(zlib.compress(helper_source())).decode("ascii")
    return shlex.join([python, "-I", "-c", _MATERIALIZE, helper_digest(), payload])


async def run_helper(
    sandbox: SandboxAdapter,
    operation: str,
    request: Mapping[str, Any],
    *,
    timeout: float,
    crashes: HelperCrashCount | None = None,
) -> JsonValue:
    """Run one helper operation in the sandbox and return its result.

    The helper runs from the sandbox's root directory: a working directory the
    operation needs goes in ``request``.
    Installs the helper first when the sandbox holds no matching copy, and
    streams a request larger than :data:`REQUEST_CHUNK_BYTES` into a file.
    Raises :class:`SandboxToolError` with the helper's message when the tool
    fails, :class:`SandboxToolTimeoutError` when the sandbox stops a command
    that outlived ``timeout``, and :class:`SandboxUnavailableError` when the
    adapter fails or the helper cannot be run at all.

    A helper that started and then exited without answering crashed. With
    ``crashes``, the count for a tool call, that raises
    :class:`SandboxHelperCrashedError` until :data:`HELPER_CRASH_LIMIT` crashes
    in a row, and :class:`SandboxUnavailableError` then. Without it, a crash
    raises :class:`SandboxUnavailableError` at once.
    """
    encoded = _sandbox_helper.encode_request(request)
    if len(encoded) <= REQUEST_CHUNK_BYTES:
        command = helper_command(sandbox.python, operation, "--request-base64", encoded)
    else:
        path = await _upload_request(sandbox, encoded)
        command = helper_command(sandbox.python, operation, "--request-file", path)
    result = await _execute(sandbox, command, timeout=timeout)
    if result.exit_code == HELPER_MISSING_EXIT_CODE:
        materialized = await _set_up(
            sandbox,
            "install the tool helper",
            materialize_command(sandbox.python),
            timeout=_MATERIALIZE_TIMEOUT_SECONDS,
        )
        if materialized.exit_code != 0:
            raise SandboxUnavailableError(
                _failure("install the tool helper", materialized)
            )
        result = await _execute(sandbox, command, timeout=timeout)
    if result.exit_code != 0:
        failure = _failure("run the tool helper", result)
        if crashes is None or result.exit_code in _HELPER_NOT_STARTED_EXIT_CODES:
            raise SandboxUnavailableError(failure)
        raise crashes.crashed(failure)
    if crashes is not None:
        crashes.answered()
    try:
        response = _HELPER_RESPONSE.validate_json(result.stdout)
    except ValidationError as exc:
        raise SandboxToolError(
            _failure("read the tool helper's response", result)
        ) from exc
    match response:
        case _HelperSucceeded(result=value):
            return value
        case _HelperFailed(error=error):
            raise SandboxToolError(error)


async def _upload_request(sandbox: SandboxAdapter, encoded: str) -> str:
    chunks = [
        encoded[start : start + REQUEST_CHUNK_BYTES]
        for start in range(0, len(encoded), REQUEST_CHUNK_BYTES)
    ]
    created = await _set_up(
        sandbox,
        "store the tool request",
        shlex.join([sandbox.python, "-I", "-c", _CREATE_REQUEST_FILE, chunks[0]]),
        timeout=_UPLOAD_TIMEOUT_SECONDS,
    )
    if created.exit_code != 0 or not created.stdout:
        raise SandboxUnavailableError(_failure("store the tool request", created))
    path = created.stdout
    for chunk in chunks[1:]:
        appended = await _set_up(
            sandbox,
            "store the tool request",
            shlex.join([
                sandbox.python,
                "-I",
                "-c",
                _APPEND_REQUEST_CHUNK,
                path,
                chunk,
            ]),
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
        if appended.exit_code != 0:
            raise SandboxUnavailableError(_failure("store the tool request", appended))
    return path


async def _set_up(
    sandbox: SandboxAdapter, step: str, command: str, *, timeout: float
) -> SandboxExecResult:
    """Run a command that prepares a helper run, not the tool's own command.

    The sandbox stopping it at a time limit is not the tool's timeout: the
    sandbox cannot run the tool.
    """
    try:
        return await _execute(sandbox, command, timeout=timeout)
    except SandboxToolTimeoutError as exc:
        raise SandboxUnavailableError(
            f"Sandbox stopped the command to {step} at its time limit"
        ) from exc


async def _execute(
    sandbox: SandboxAdapter, command: str, *, timeout: float
) -> SandboxExecResult:
    try:
        return await sandbox.execute(command, _HELPER_CWD, timeout)
    except SandboxCommandTimeoutError as exc:
        raise SandboxToolTimeoutError(exc.timeout) from exc
    except TimeoutError as exc:
        detail = str(exc) or "no answer in time"
        raise SandboxUnavailableError(
            f"Sandbox did not answer a command with a {timeout:g}s timeout: {detail}"
        ) from exc
    except SANDBOX_FAILURES as exc:
        raise SandboxUnavailableError(f"Sandbox failed: {exc}") from exc


def _failure(stage: str, result: SandboxExecResult) -> str:
    detail = (result.stderr or result.stdout).strip()[-2_000:]
    message = f"Sandbox could not {stage} (exit code {result.exit_code})"
    return f"{message}: {detail}" if detail else message


__all__ = [
    "HELPER_CRASH_LIMIT",
    "HELPER_MISSING_EXIT_CODE",
    "REQUEST_CHUNK_BYTES",
    "SANDBOX_FAILURES",
    "SANDBOX_UNAVAILABLE_CODE",
    "HelperCrashCount",
    "SandboxAdapter",
    "SandboxCommandTimeoutError",
    "SandboxExecResult",
    "SandboxHelperCrashedError",
    "SandboxToolError",
    "SandboxToolTimeoutError",
    "SandboxUnavailableError",
    "helper_command",
    "helper_digest",
    "helper_source",
    "materialize_command",
    "run_helper",
    "sandbox_path",
]
