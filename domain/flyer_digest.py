"""Daily publication summaries, independent of persistence and push providers."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
DIGEST_TIME = time(10)
MAX_SUMMARY_NAMES = 3


class DigestConfigurationError(RuntimeError):
    """Durable daily delivery needs the configured direct database connection."""


def digest_opens(now: datetime, *, tomorrow: bool = False) -> datetime:
    local = now.astimezone(ROME)
    day = local.date() + timedelta(days=int(tomorrow))
    return datetime.combine(day, DIGEST_TIME, ROME)


@dataclass(frozen=True)
class FlyerDigest:
    user_id: str
    day: str
    flyers: tuple[dict, ...]

    @property
    def key(self) -> str:
        return f"flyer-digest:{self.user_id}:{self.day}"

    def data(self) -> dict:
        return {
            "kind": "flyer_published", "aggregation_key": self.key,
            # Older mobile clients deduplicate their local push using this representative ID.
            "flyer_id": self.flyers[0]["flyer_id"],
            "flyer_ids": list(dict.fromkeys(f["flyer_id"] for f in self.flyers)),
            "supermarket_ids": list(dict.fromkeys(f["supermarket_id"] for f in self.flyers)),
            "url": "/volantini",
        }

    def body(self) -> str:
        names = {f["supermarket_name"].strip().casefold(): f["supermarket_name"].strip()
                 for f in self.flyers}
        labels = sorted(names.values(), key=str.casefold)
        remaining = len(labels) - MAX_SUMMARY_NAMES
        suffix = " e un altro supermercato" if remaining == 1 else (
            f" e altri {remaining} supermercati" if remaining > 1 else "")
        return f"Novità da {', '.join(labels[:MAX_SUMMARY_NAMES])}{suffix}."
