"""One independent normalizer per ATS, all returning the same Job dataclass."""

from collections.abc import Callable

from normalizers import ashby, greenhouse, lever, workday
from normalizers.models import Job

REGISTRY: dict[str, Callable[[dict, dict], Job]] = {
    "greenhouse": greenhouse.normalize,
    "lever": lever.normalize,
    "ashby": ashby.normalize,
    "workday": workday.normalize,
}


def get_normalizer(ats: str) -> Callable[[dict, dict], Job]:
    try:
        return REGISTRY[ats]
    except KeyError:
        raise KeyError(f"Unknown ATS {ats!r}; expected one of {', '.join(REGISTRY)}") from None
