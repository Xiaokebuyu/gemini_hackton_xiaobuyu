"""
世界初始化主编排器

协调地图加载、角色加载等流程，完成世界数据初始化。
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional

from google.cloud import firestore

from app.config import settings
from app.tools.worldbook_graphizer.models import MapsData, CharactersData
from .map_loader import MapLoader
from .character_loader import CharacterLoader
from .graph_prefill_loader import GraphPrefillLoader


class WorldInitializer:
    """世界初始化器"""

    def __init__(self, firestore_client: Optional[firestore.Client] = None):
        """
        初始化

        Args:
            firestore_client: Firestore 客户端（可选）
        """
        self.db = firestore_client or firestore.Client(
            database=settings.firestore_database
        )
        self.map_loader = MapLoader(firestore_client=self.db)
        self.character_loader = CharacterLoader(firestore_client=self.db)
        self.graph_prefill_loader = GraphPrefillLoader(firestore_client=self.db)

    async def initialize(
        self,
        world_id: str,
        data_dir: Path,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        执行完整的世界初始化

        Args:
            world_id: 世界 ID
            data_dir: 结构化数据目录（包含 maps.json, characters.json 等）
            dry_run: 是否只模拟执行
            verbose: 是否输出详细信息

        Returns:
            初始化结果统计
        """
        results = {
            "world_id": world_id,
            "maps": {},
            "characters": {},
            "world_map": False,
            "combat_entities": {},
            "errors": [],
        }

        if verbose:
            mode = "[DRY RUN] " if dry_run else ""
            print(f"\n{mode}Initializing world: {world_id}")
            print(f"Data directory: {data_dir}")

        # 检查数据目录
        if not data_dir.exists():
            error = f"Data directory not found: {data_dir}"
            results["errors"].append(error)
            if verbose:
                print(f"ERROR: {error}")
            return results

        # strict-v2：完整导入前先校验章节编排数据，避免半初始化状态
        prefill_path = data_dir / "prefilled_graph.json"
        chapters_path = data_dir / "chapters_v2.json"
        mainlines_path = data_dir / "mainlines.json"
        if (
            settings.narrative_v2_strict_mode
            and prefill_path.exists()
            and chapters_path.exists()
            and mainlines_path.exists()
        ):
            try:
                chapters_v2 = json.loads(chapters_path.read_text(encoding="utf-8"))
                mainlines_payload = json.loads(mainlines_path.read_text(encoding="utf-8"))
                mainlines_raw = (
                    mainlines_payload.get("mainlines", [])
                    if isinstance(mainlines_payload, dict)
                    else []
                )
                chapters_v2, mainlines_raw = GraphPrefillLoader.upgrade_narrative_v2_artifacts(
                    chapters_v2=chapters_v2,
                    mainlines_raw=mainlines_raw,
                )
                GraphPrefillLoader.validate_narrative_v2_artifacts(
                    chapters_v2=chapters_v2,
                    mainlines_raw=mainlines_raw,
                    data_dir=data_dir,
                )
            except Exception as e:
                error = f"Strict v2 validation failed before initialization: {e}"
                results["errors"].append(error)
                if verbose:
                    print(f"ERROR: {error}")
                return results

        # 阶段 1: 加载地图
        maps_path = data_dir / "maps.json"
        if maps_path.exists():
            if verbose:
                print(f"\n[Phase 1] Loading maps from {maps_path}...")
            try:
                maps_raw = json.loads(maps_path.read_text(encoding="utf-8"))
                maps_data = MapsData(**maps_raw)
                results["maps"] = await self.map_loader.load_maps(
                    world_id=world_id,
                    maps_data=maps_data,
                    dry_run=dry_run,
                    verbose=verbose,
                )
            except Exception as e:
                error = f"Failed to load maps: {str(e)}"
                results["errors"].append(error)
                if verbose:
                    print(f"ERROR: {error}")
        else:
            if verbose:
                print(f"\n[Phase 1] Skipping maps (file not found: {maps_path})")

        # 阶段 2: 加载世界地图
        world_map_path = data_dir / "world_map.json"
        if world_map_path.exists():
            if verbose:
                print(f"\n[Phase 2] Loading world map from {world_map_path}...")
            try:
                await self.map_loader.load_world_map(
                    world_id=world_id,
                    world_map_path=world_map_path,
                    dry_run=dry_run,
                    verbose=verbose,
                )
                results["world_map"] = True
            except Exception as e:
                error = f"Failed to load world map: {str(e)}"
                results["errors"].append(error)
                if verbose:
                    print(f"ERROR: {error}")
        else:
            if verbose:
                print(f"\n[Phase 2] Skipping world map (file not found)")

        # 阶段 3: 加载角色
        chars_path = data_dir / "characters.json"
        profiles_path = data_dir / "character_profiles.json"

        if chars_path.exists():
            if verbose:
                print(f"\n[Phase 3] Loading characters from {chars_path}...")
            try:
                chars_raw = json.loads(chars_path.read_text(encoding="utf-8"))
                chars_data = CharactersData(**chars_raw)
                results["characters"] = await self.character_loader.load_characters(
                    world_id=world_id,
                    characters_data=chars_data,
                    dry_run=dry_run,
                    verbose=verbose,
                )
            except Exception as e:
                error = f"Failed to load characters: {str(e)}"
                results["errors"].append(error)
                if verbose:
                    print(f"ERROR: {error}")
        elif profiles_path.exists():
            # 备选：从 profiles 文件加载
            if verbose:
                print(f"\n[Phase 3] Loading character profiles from {profiles_path}...")
            try:
                results["characters"] = await self.character_loader.load_profiles_from_file(
                    world_id=world_id,
                    profiles_path=profiles_path,
                    dry_run=dry_run,
                    verbose=verbose,
                )
            except Exception as e:
                error = f"Failed to load profiles: {str(e)}"
                results["errors"].append(error)
                if verbose:
                    print(f"ERROR: {error}")
        else:
            if verbose:
                print(f"\n[Phase 3] Skipping characters (no data file found)")

        # 阶段 4: 图谱预填充（v2 scope 写入）
        prefill_path = data_dir / "prefilled_graph.json"
        if prefill_path.exists():
            if verbose:
                print(f"\n[Phase 4] Loading graph prefill from {data_dir}...")
            try:
                results["graph_prefill"] = await self.graph_prefill_loader.load_prefilled_graph(
                    world_id=world_id,
                    data_dir=data_dir,
                    dry_run=dry_run,
                    verbose=verbose,
                )
            except Exception as e:
                error = f"Failed to load graph prefill: {str(e)}"
                results["errors"].append(error)
                if verbose:
                    print(f"ERROR: {error}")
        else:
            if verbose:
                print(f"\n[Phase 4] Skipping graph prefill (file not found: {prefill_path})")

        # 阶段 5: 加载战斗实体（monsters/skills/items）
        if verbose:
            print(f"\n[Phase 5] Loading combat entities from {data_dir}...")
        try:
            results["combat_entities"] = await self._load_combat_entities(
                world_id=world_id,
                data_dir=data_dir,
                dry_run=dry_run,
                verbose=verbose,
            )
        except Exception as e:
            error = f"Failed to load combat entities: {str(e)}"
            results["errors"].append(error)
            if verbose:
                print(f"ERROR: {error}")

        # 打印总结
        if verbose:
            print("\n" + "=" * 50)
            print("Initialization Summary")
            print("=" * 50)
            if results["maps"]:
                print(f"Maps loaded: {results['maps'].get('maps_loaded', 0)}")
                print(f"Passerby templates: {results['maps'].get('passerby_templates_loaded', 0)}")
            if results["characters"]:
                print(f"Main characters: {results['characters'].get('main_loaded', 0)}")
                print(f"Secondary characters: {results['characters'].get('secondary_loaded', 0)}")
                print(f"Map assignments: {results['characters'].get('map_assignments_loaded', 0)}")
            print(f"World map: {'Yes' if results['world_map'] else 'No'}")
            if results.get("graph_prefill"):
                gp = results["graph_prefill"]
                print(f"Graph prefill: {gp.get('nodes_written', 0)} nodes, "
                      f"{gp.get('edges_written', 0)} edges, "
                      f"{gp.get('chapters_meta_written', 0)} chapter metas, "
                      f"{gp.get('mainlines_meta_written', 0)} mainline metas, "
                      f"{gp.get('dispositions_written', 0)} dispositions")
            ce = results.get("combat_entities") or {}
            if ce:
                print(
                    "Combat entities: "
                    f"monsters={ce.get('monsters_loaded', 0)}, "
                    f"skills={ce.get('skills_loaded', 0)}, "
                    f"items={ce.get('items_loaded', 0)}"
                )
            if results["errors"]:
                print(f"\nErrors: {len(results['errors'])}")
                for err in results["errors"]:
                    print(f"  - {err}")

        return results

    async def initialize_maps_only(
        self,
        world_id: str,
        maps_path: Path,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """仅加载地图数据"""
        if not maps_path.exists():
            raise FileNotFoundError(f"Maps file not found: {maps_path}")

        maps_raw = json.loads(maps_path.read_text(encoding="utf-8"))
        maps_data = MapsData(**maps_raw)

        return await self.map_loader.load_maps(
            world_id=world_id,
            maps_data=maps_data,
            dry_run=dry_run,
            verbose=verbose,
        )

    async def initialize_character(
        self,
        world_id: str,
        character_id: str,
        chars_path: Path,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> bool:
        """加载单个角色"""
        if not chars_path.exists():
            raise FileNotFoundError(f"Characters file not found: {chars_path}")

        chars_raw = json.loads(chars_path.read_text(encoding="utf-8"))
        chars_data = CharactersData(**chars_raw)

        return await self.character_loader.load_single_character(
            world_id=world_id,
            char_id=character_id,
            characters_data=chars_data,
            dry_run=dry_run,
            verbose=verbose,
        )

    @staticmethod
    def _flatten_entity_entries(raw: Any) -> list[dict]:
        entries: list[dict] = []
        if isinstance(raw, dict):
            looks_like_entry = any(
                key in raw for key in ("id", "name", "type", "stats", "effect", "properties")
            )
            if looks_like_entry:
                entries.append(raw)
            else:
                for value in raw.values():
                    entries.extend(WorldInitializer._flatten_entity_entries(value))
            return entries
        if isinstance(raw, list):
            for item in raw:
                entries.extend(WorldInitializer._flatten_entity_entries(item))
        return entries

    async def _load_combat_entities(
        self,
        world_id: str,
        data_dir: Path,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """Load monsters/skills/items into Firestore combat_entities collection."""
        stats: Dict[str, Any] = {
            "monsters_loaded": 0,
            "skills_loaded": 0,
            "items_loaded": 0,
            "errors": [],
        }

        world_ref = self.db.collection("worlds").document(world_id)
        combat_ref = world_ref.collection("combat_entities")

        for entity_type in ("monsters", "skills", "items"):
            file_path = data_dir / f"{entity_type}.json"
            if not file_path.exists():
                if verbose:
                    print(f"  - skip {entity_type}: file not found")
                continue

            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                entries = self._flatten_entity_entries(payload)
                if verbose:
                    print(f"  - {entity_type}: {len(entries)} entries")

                if not dry_run:
                    combat_ref.document(entity_type).set(
                        {
                            "entries": entries,
                            "source_file": str(file_path),
                            "version": "structured_new" if "structured_new" in str(file_path) else "structured",
                            "entry_count": len(entries),
                            "updated_at": firestore.SERVER_TIMESTAMP,
                        },
                        merge=True,
                    )

                stats[f"{entity_type}_loaded"] = len(entries)
            except Exception as exc:
                error_msg = f"{entity_type} load failed: {exc}"
                stats["errors"].append(error_msg)
                if verbose:
                    print(f"    ERROR: {error_msg}")

        return stats

    async def verify_initialization(
        self,
        world_id: str,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        验证世界初始化状态

        Args:
            world_id: 世界 ID
            verbose: 是否输出详细信息

        Returns:
            验证结果
        """
        results = {
            "world_id": world_id,
            "maps": [],
            "characters": [],
            "world_map_exists": False,
        }

        if verbose:
            print(f"\nVerifying world: {world_id}")

        # 检查地图
        try:
            maps = await self.map_loader.list_maps(world_id)
            results["maps"] = maps
            if verbose:
                print(f"  Maps: {len(maps)}")
                for m in maps[:5]:
                    print(f"    - {m}")
                if len(maps) > 5:
                    print(f"    ... and {len(maps) - 5} more")
        except Exception as e:
            if verbose:
                print(f"  Maps: Error - {str(e)}")

        # 检查角色
        try:
            characters = await self.character_loader.list_characters(world_id)
            results["characters"] = characters
            if verbose:
                print(f"  Characters: {len(characters)}")
                for c in characters[:5]:
                    print(f"    - {c}")
                if len(characters) > 5:
                    print(f"    ... and {len(characters) - 5} more")
        except Exception as e:
            if verbose:
                print(f"  Characters: Error - {str(e)}")

        # 检查世界地图
        try:
            meta_ref = self.map_loader._get_world_meta_ref(world_id)
            doc = meta_ref.get()
            results["world_map_exists"] = doc.exists
            if verbose:
                print(f"  World map: {'Yes' if doc.exists else 'No'}")
        except Exception as e:
            if verbose:
                print(f"  World map: Error - {str(e)}")

        return results
