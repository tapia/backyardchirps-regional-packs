"""
The ground a pack covers.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    """
    A box in degrees. West and south are the lower corner.
    """

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        """
        Refuse a box that cannot describe ground, so that holding one proves it does. A box
        crossing the antimeridian is refused rather than wrapped: nothing needs one yet, and a
        silently wrapped box would crop every raster to the wrong half of the world.
        """
        if not (-180.0 <= self.west <= 180.0 and -180.0 <= self.east <= 180.0):
            raise ValueError("Longitudes have to be between -180 and 180.")
        if not (-90.0 <= self.south <= 90.0 and -90.0 <= self.north <= 90.0):
            raise ValueError("Latitudes have to be between -90 and 90.")
        if self.west >= self.east:
            raise ValueError("West has to be less than east. A box crossing the antimeridian is not supported.")
        if self.south >= self.north:
            raise ValueError("South has to be less than north.")

    def grid_points(self, step_degrees: float) -> list[tuple[float, float]]:
        """
        Points covering the box, as (latitude, longitude), including all four corners. The
        spacing is at most step_degrees and is spread evenly, so a box is never asked about
        beyond its own edge and never leaves a wider gap at one end than at the other.
        """
        if step_degrees <= 0:
            raise ValueError("The grid step has to be a positive number of degrees.")
        return [
            (latitude, longitude)
            for latitude in _axis_points(self.south, self.north, step_degrees)
            for longitude in _axis_points(self.west, self.east, step_degrees)
        ]

    def as_dict(self) -> dict[str, float]:
        return {"west": self.west, "south": self.south, "east": self.east, "north": self.north}


def _axis_points(low: float, high: float, step: float) -> list[float]:
    if high <= low:
        return [low]
    count = math.ceil((high - low) / step)
    return [low + (high - low) * index / count for index in range(count + 1)]
