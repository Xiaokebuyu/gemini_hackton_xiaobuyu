from app.combat.combat_engine import CombatEngine
from app.combat.models.combat_session import CombatSession
from app.combat.models.combatant import Combatant, CombatantType


def _session_with_enemy(enemy_id: str) -> CombatSession:
    session = CombatSession(combat_id="c1")
    session.combatants = [
        Combatant(
            id="player",
            name="玩家",
            combatant_type=CombatantType.PLAYER,
            hp=10,
            max_hp=10,
            ac=10,
            attack_bonus=2,
            damage_dice="1d6",
            damage_bonus=1,
        ),
        Combatant(
            id=enemy_id,
            name="敌人",
            combatant_type=CombatantType.ENEMY,
            hp=8,
            max_hp=8,
            ac=10,
            attack_bonus=1,
            damage_dice="1d4",
            damage_bonus=0,
        ),
    ]
    return session


def test_parse_spell_action_id_supports_spell_and_target_underscores():
    engine = CombatEngine()
    session = _session_with_enemy("goblin_1")

    parsed = engine._parse_spell_action_id("spell_fire_bolt_goblin_1", session)
    assert parsed == ("fire_bolt", "goblin_1")

    parsed_cn = engine._parse_spell_action_id("spell_角色_火焰箭_goblin_1", session)
    assert parsed_cn == ("角色_火焰箭", "goblin_1")
