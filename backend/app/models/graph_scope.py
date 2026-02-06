"""
GraphScope: unified addressing for hierarchical graph storage.

Maps scope_type + identifiers to Firestore paths:
  world    -> worlds/{wid}/graphs/world/
  chapter  -> worlds/{wid}/chapters/{cid}/graph/
  area     -> worlds/{wid}/chapters/{cid}/areas/{aid}/graph/
  location -> worlds/{wid}/chapters/{cid}/areas/{aid}/locations/{lid}/graph/
  character -> worlds/{wid}/characters/{char_id}/
  camp     -> worlds/{wid}/camp/graph/
"""
from dataclasses import dataclass
from typing import Optional


VALID_SCOPE_TYPES = {"world", "chapter", "area", "location", "character", "camp"}


@dataclass(frozen=True)
class GraphScope:
    """Immutable scope descriptor for graph storage addressing."""

    scope_type: str
    chapter_id: Optional[str] = None
    area_id: Optional[str] = None
    location_id: Optional[str] = None
    character_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.scope_type not in VALID_SCOPE_TYPES:
            raise ValueError(
                f"Invalid scope_type '{self.scope_type}', "
                f"must be one of {VALID_SCOPE_TYPES}"
            )
        if self.scope_type == "chapter" and not self.chapter_id:
            raise ValueError("chapter scope requires chapter_id")
        if self.scope_type == "area" and (not self.chapter_id or not self.area_id):
            raise ValueError("area scope requires chapter_id and area_id")
        if self.scope_type == "location" and (
            not self.chapter_id or not self.area_id or not self.location_id
        ):
            raise ValueError(
                "location scope requires chapter_id, area_id, and location_id"
            )
        if self.scope_type == "character" and not self.character_id:
            raise ValueError("character scope requires character_id")

    @staticmethod
    def world() -> "GraphScope":
        return GraphScope(scope_type="world")

    @staticmethod
    def chapter(chapter_id: str) -> "GraphScope":
        return GraphScope(scope_type="chapter", chapter_id=chapter_id)

    @staticmethod
    def area(chapter_id: str, area_id: str) -> "GraphScope":
        return GraphScope(scope_type="area", chapter_id=chapter_id, area_id=area_id)

    @staticmethod
    def location(
        chapter_id: str, area_id: str, location_id: str
    ) -> "GraphScope":
        return GraphScope(
            scope_type="location",
            chapter_id=chapter_id,
            area_id=area_id,
            location_id=location_id,
        )

    @staticmethod
    def character(character_id: str) -> "GraphScope":
        return GraphScope(scope_type="character", character_id=character_id)

    @staticmethod
    def camp() -> "GraphScope":
        return GraphScope(scope_type="camp")
