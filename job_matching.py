"""Canonical job filtering with explicit locations and boundary-aware keywords."""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from normalizers.models import Job

DEFAULT_RULES = Path(__file__).with_name("job_filters.yaml")
_TRADITIONAL = str.maketrans({
    "開": "开", "發": "发", "應": "应", "體": "体", "語": "语", "機": "机",
    "學": "学", "習": "习", "屆": "届", "畢": "毕", "業": "业", "調": "调",
    "規": "规", "劃": "划", "檢": "检", "索": "索", "強": "强", "軟": "软",
})


def canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(_TRADITIONAL)
    return " ".join(text.split())


def _pattern(terms: list[str]) -> re.Pattern[str]:
    alternatives = []
    for term in terms:
        normalized = canonical(term)
        if re.search(r"[\u3400-\u9fff]", normalized):
            alternatives.append(re.escape(normalized))
        else:
            parts = re.split(r"[\s-]+", normalized)
            phrase = r"[\s\-\u2010-\u2015]+".join(re.escape(part) for part in parts)
            alternatives.append(r"(?<![a-z0-9])" + phrase + r"(?![a-z0-9])")
    return re.compile("|".join(alternatives))


@dataclass(frozen=True)
class JobFilter:
    locations: re.Pattern[str]
    exclude: re.Pattern[str]
    include: re.Pattern[str]

    def rejection(self, job: Job) -> str | None:
        if not self.locations.search(canonical(job.location)):
            return "location"
        # Check each field independently so phrases cannot span unrelated fields.
        fields = [canonical(value) for value in (
            job.company, job.title, job.department or "", job.description,
        )]
        if any(self.exclude.search(field) for field in fields):
            return "excluded"
        if not any(self.include.search(field) for field in fields[1:]):
            return "keyword"
        return None


def load_filter(path: Path = DEFAULT_RULES) -> JobFilter:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"{path}: filter rules must be a mapping")
    patterns = {}
    for name in ("locations", "exclude", "include"):
        terms = config.get(name)
        if not isinstance(terms, list) or not terms or not all(
            isinstance(term, str) and term.strip() for term in terms
        ):
            raise ValueError(f"{path}: {name} must be a nonempty list of nonempty strings")
        patterns[name] = _pattern(terms)
    return JobFilter(**patterns)
