from __future__ import annotations


def leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """If the exception is a BaseExceptionGroup, return a list of all leaf exceptions.
    Otherwise, return a list containing the exception itself.
    """
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in leaf_exceptions(inner)]
    return [exc]


def first_of_type[E: BaseException](exc: BaseException, target: type[E]) -> E | None:
    if isinstance(exc, target):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            if (found := first_of_type(inner, target)) is not None:
                return found
    return None


def describe_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
