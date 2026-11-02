"""Shared text and publication-date handling; never infer dates from crawl time."""

import logging
from datetime import UTC, datetime
from html.parser import HTMLParser

log = logging.getLogger(__name__)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_text(value: str | None) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    parser.close()
    return " ".join("".join(parser.parts).split())


def plain_text(value: str | None) -> str:
    return " ".join((value or "").split())


def required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def job_id(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return required_text(value, "ats_job_id")


def company_name(company: dict) -> str:
    return required_text(
        company.get("name") or company.get("company")
        or company.get("slug") or company.get("tenant"),
        "company",
    )


def published_at(value: object, *, milliseconds: bool = False) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        if milliseconds:
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError("expected milliseconds since epoch")
            result = datetime.fromtimestamp(value / 1000, tz=UTC)
        elif isinstance(value, str):
            result = datetime.fromisoformat(value)
            result = result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
        else:
            raise ValueError("expected an ISO publication date")
        if result <= EPOCH:
            raise ValueError("year-one or zero/pre-epoch publication date")
    except (ValueError, OverflowError, OSError) as exc:
        log.warning("Invalid publication date %r: %s; storing NULL, not crawl time", value, exc)
        return None
    return result


def remote_flag(workplace: object, fallback: object = None) -> bool | None:
    if isinstance(workplace, dict):
        workplace = workplace.get("descriptor")
    if isinstance(workplace, str):
        key = workplace.lower().replace("-", "").replace(" ", "")
        if key == "remote":
            return True
        if key in {"hybrid", "onsite"}:
            return False
    return fallback if isinstance(fallback, bool) else None
