"""Print live raw job JSON without touching notification or seen-job state."""

import argparse
import json
import logging

import requests

from fetchers import REGISTRY, get_fetcher

log = logging.getLogger("fetchers")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ats", choices=REGISTRY)
    parser.add_argument("--slug", help="Board slug for Greenhouse, Lever or Ashby")
    parser.add_argument("--host", help="Workday hostname (without https://)")
    parser.add_argument("--tenant")
    parser.add_argument("--site")
    parser.add_argument(
        "--max-jobs", type=int, default=2000, help="Workday fetch cap, at most 2000"
    )
    parser.add_argument("--sample", type=int, default=1, help="Jobs to print; 0 prints all fetched")
    args = parser.parse_args(argv)
    if args.sample < 0:
        parser.error("--sample must be nonnegative")
    if args.ats == "workday":
        if not all((args.host, args.tenant, args.site)):
            parser.error("Workday requires --host, --tenant and --site")
    elif not args.slug:
        parser.error(f"{args.ats} requires --slug")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        jobs = get_fetcher(args.ats)(vars(args))
        log.info("%s: fetched %d jobs", args.ats, len(jobs))
        if not jobs:
            log.error("No live jobs returned; verify the board slug and possible ATS migration")
            return 1
        print(json.dumps(jobs[:args.sample] if args.sample else jobs, ensure_ascii=True, indent=2))
    except (requests.RequestException, ValueError, KeyError):
        log.exception("%s fetch failed", args.ats)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
