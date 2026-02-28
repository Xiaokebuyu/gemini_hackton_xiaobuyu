from typing import Any

from app.game_core.content.base import ContentRegistry
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.content.registries.items import ItemRegistry
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.registries.monsters import MonsterRegistry
from app.game_core.content.registries.tag import TagRegistry
from app.game_core.content.world import WorldInstance


class RecordingRegistry(ContentRegistry):
    def __init__(
        self,
        name: str,
        calls: list[str],
        validation_issues: list[str] | None = None,
    ) -> None:
        super().__init__(name)
        self._calls = calls
        self._validation_issues = list(validation_issues or [])
        self._items: dict[str, Any] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._calls.append(self.name)
        self._items = dict(data)

    def get(self, content_id: str) -> Any | None:
        return self._items.get(content_id)

    def list_all(self) -> list[Any]:
        return list(self._items.values())

    def validate(self) -> list[str]:
        return list(self._validation_issues)


def test_world_instance_load_all_uses_dependency_order_before_insertion_order():
    calls: list[str] = []
    world = WorldInstance("test_world")
    world.register(RecordingRegistry("custom", calls))
    world.register(RecordingRegistry("characters", calls))
    world.register(RecordingRegistry("tags", calls))
    world.register(RecordingRegistry("items", calls))
    world.register(RecordingRegistry("maps", calls))
    world.register(RecordingRegistry("skills", calls))

    world.load_all({})

    assert calls == ["tags", "maps", "skills", "characters", "items", "custom"]


def test_world_instance_validate_reports_registry_and_cross_registry_issues():
    world = WorldInstance("test_world")

    tags = TagRegistry()
    tags.load({"terrain": {"id": "terrain", "tags": "bad"}})

    maps = MapRegistry()
    maps.load({"town": {"id": "town"}})

    items = ItemRegistry()
    items.load({"rope": {"id": "rope"}})

    characters = CharacterRegistry()
    characters.load(
        {
            "merchant": {
                "id": "merchant",
                "area_id": "missing_map",
                "inventory": [{"item_id": "missing_item"}],
                "shop": {"inventory": [{"item_id": "missing_shop_item"}]},
            }
        }
    )

    monsters = MonsterRegistry()
    monsters.load(
        {
            "goblin": {
                "id": "goblin",
                "loot_table": [{"item_id": "missing_loot"}],
            }
        }
    )

    for registry in (tags, maps, items, characters, monsters):
        world.register(registry)

    issues = world.validate()

    assert issues["tags"] == ["tag dimension 'terrain' has invalid tags"]
    assert "_world" in issues
    assert (
        "character 'merchant' references unknown map 'missing_map' via area_id"
        in issues["_world"]
    )
    assert (
        "character 'merchant' references unknown item 'missing_item' via inventory[0]"
        in issues["_world"]
    )
    assert (
        "character 'merchant' references unknown item 'missing_shop_item' via shop.inventory[0]"
        in issues["_world"]
    )
    assert (
        "monster 'goblin' references unknown item 'missing_loot' via loot_table[0]"
        in issues["_world"]
    )


def test_world_instance_validate_returns_empty_dict_when_all_registered_data_is_valid():
    world = WorldInstance("test_world")

    maps = MapRegistry()
    maps.load({"town": {"id": "town", "starting_area": True}})

    items = ItemRegistry()
    items.load({"rope": {"id": "rope", "price": 1}})

    characters = CharacterRegistry()
    characters.load(
        {
            "merchant": {
                "id": "merchant",
                "area_id": "town",
                "inventory": [{"item_id": "rope", "count": 1}],
                "shop": {"inventory": [{"item_id": "rope", "price": 2}]},
            }
        }
    )

    monsters = MonsterRegistry()
    monsters.load(
        {
            "goblin": {
                "id": "goblin",
                "hp": 7,
                "ac": 13,
                "loot_table": [{"item_id": "rope", "chance": 0.5}],
            }
        }
    )

    for registry in (maps, items, characters, monsters):
        world.register(registry)

    assert world.validate() == {}
