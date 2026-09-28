"""Small, serializable input types shared by offline and HA runs."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math

UTC = timezone.utc


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(UTC)


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Reading:
    value: object
    reported: str | None

    def fresh(self, now: datetime, seconds: int) -> bool:
        try:
            age = (now - instant(self.reported)).total_seconds()
            return -60 <= age <= seconds and self.value not in (None, "unknown", "unavailable")
        except (TypeError, ValueError, AttributeError):
            return False


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    price: float

    @property
    def hours(self):
        return (self.end - self.start).total_seconds() / 3600

    def json(self):
        return {"start": self.start.isoformat(), "end": self.end.isoformat(),
                "price_eur_kwh": self.price}


def reading(snapshot, key):
    raw = snapshot.get("readings", {}).get(key, {})
    return Reading(raw.get("value"), raw.get("reported"))
