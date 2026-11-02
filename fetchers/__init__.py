"""Raw ATS fetchers, independent of filtering, deduplication and notifications."""

from collections.abc import Callable

from fetchers import ashby, greenhouse, lever, workday

REGISTRY: dict[str, Callable[[dict], list[dict]]] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workday": workday.fetch,
}


def get_fetcher(ats: str) -> Callable[[dict], list[dict]]:
    try:
        return REGISTRY[ats]
    except KeyError:
        raise KeyError(f"Unknown ATS {ats!r}; expected one of {', '.join(REGISTRY)}") from None
