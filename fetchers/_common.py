"""Validation shared by the public JSON endpoints."""

TIMEOUT_SECONDS = 30


def slug(company: dict) -> str:
    value = company.get("slug") or company.get("company")
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "/?#"):
        raise ValueError("A nonempty board slug (or existing company key) is required")
    return value


def records(value: object, endpoint: str) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{endpoint}: expected a JSON list of job objects")
    return value


def object_payload(value: object, endpoint: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{endpoint}: expected a JSON object")
    return value
