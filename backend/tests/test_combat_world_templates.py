from app.combat import enemy_registry


class _RepoStub:
    def list_monsters(self):
        return [
            {
                "id": "monster_goblin_scout",
                "name": "哥布林斥候",
                "type": "humanoid",
                "challenge_rating": "白瓷",
                "stats": {"hp": 18, "ac": 14, "dex": 15},
                "attacks": [{"name": "短弓", "damage": "1d6+2"}],
                "skills": [{"name": "火焰箭"}],
                "loot": ["粗制短弓"],
            }
        ]


def test_load_world_templates_and_lookup_by_alias():
    world_id = "unit_test_world"
    enemy_registry.load_world_templates(
        world_id=world_id,
        template_version="v3",
        force_reload=True,
        repository=_RepoStub(),
    )

    template = enemy_registry.get_template(
        "monster_goblin_scout",
        world_id=world_id,
        template_version="v3",
    )
    assert template is not None
    assert template["name"] == "哥布林斥候"
    assert template["max_hp"] == 18
    assert template["damage_dice"] == "1d6+2"
    assert "spells_known" in template

    # Alias by display name should resolve to same world template.
    by_name = enemy_registry.get_template(
        "哥布林斥候",
        world_id=world_id,
        template_version="v3",
    )
    assert by_name is not None
    assert by_name["name"] == "哥布林斥候"
