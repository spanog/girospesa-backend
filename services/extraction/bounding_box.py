"""Pure value object for normalized, versioned product coordinates."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
import math

GRID_SIZE = 1000


class BoundingBoxVersion(IntEnum):
    LEGACY_YX = 1
    XY = 2


class InvalidBoundingBox(ValueError):
    """Coordinates cannot describe a normalized product rectangle."""


@dataclass(frozen=True)
class BoundingBox:
    x_min: int
    y_min: int
    x_max: int
    y_max: int

    def __post_init__(self) -> None:
        if not (0 <= self.x_min < self.x_max <= GRID_SIZE and 0 <= self.y_min < self.y_max <= GRID_SIZE):
            raise InvalidBoundingBox("Coordinates must increase within the 0–1000 grid")

    @classmethod
    def parse(cls, value: object, version: int = BoundingBoxVersion.XY) -> "BoundingBox":
        try:
            order = BoundingBoxVersion(version)
            if isinstance(value, Mapping):
                values = [value[key] for key in ("x_min", "y_min", "x_max", "y_max")]
            elif isinstance(value, Sequence) and not isinstance(value, str) and len(value) == 4:
                values = list(value)
                if order == BoundingBoxVersion.LEGACY_YX:
                    values = [values[1], values[0], values[3], values[2]]
            else:
                raise InvalidBoundingBox("Expected four coordinates or named fields")
            return cls(*(_coordinate(item) for item in values))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise InvalidBoundingBox(f"Invalid bounding box: {exc}") from exc

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x_min, self.y_min, self.x_max, self.y_max

    def as_list(self) -> list[int]:
        return list(self.as_tuple())


def _coordinate(value: object) -> int:
    if isinstance(value, bool):
        raise InvalidBoundingBox("Boolean coordinates are invalid")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= GRID_SIZE:
        raise InvalidBoundingBox("Coordinates must be finite and within 0–1000")
    return int(number)


def stored_box(row: Mapping) -> list[int] | None:
    """Read old rows without changing their historical geometry."""
    try:
        return BoundingBox.parse(row.get("packshot_bbox"), row.get("packshot_bbox_version", BoundingBoxVersion.LEGACY_YX)).as_list()
    except InvalidBoundingBox:
        return None
