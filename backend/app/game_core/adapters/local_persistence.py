"""Local filesystem implementation of the persistence port."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping


class LocalFilePersistencePort:
    """Persist session payloads into the canonical local save directory."""

    def __init__(self, base_dir: str | Path = "saves") -> None:
        self._base_dir = Path(base_dir)

    async def load(self, key: str) -> dict[str, Any]:
        """Load one session payload from local files."""
        session_dir = self._session_dir(key)
        if not session_dir.exists():
            return {}
        if not session_dir.is_dir():
            raise ValueError(f"session path is not a directory: {session_dir}")

        state_dir = session_dir / "state"
        if state_dir.exists() and not state_dir.is_dir():
            raise ValueError(f"state path is not a directory: {state_dir}")

        state_payload: dict[str, dict[str, Any]] = {}
        if state_dir.exists():
            for path in sorted(state_dir.glob("*.json")):
                state_payload[path.stem] = self._read_json_file(path)

        meta_path = session_dir / "meta.json"
        meta_payload = (
            self._read_json_file(meta_path) if meta_path.exists() else {}
        )

        if not state_payload and not meta_payload:
            return {}
        return {
            "state": state_payload,
            "meta": meta_payload,
        }

    async def save(self, key: str, payload: dict[str, Any]) -> None:
        """Persist one session payload to local files."""
        session_dir = self._session_dir(key)
        state_dir = session_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        state_payload, meta_payload = self._normalize_payload(payload)

        current_slice_names = set(state_payload.keys())
        for path in state_dir.glob("*.json"):
            if path.stem not in current_slice_names:
                path.unlink()

        for slice_name, slice_payload in state_payload.items():
            self._atomic_write_json(state_dir / f"{slice_name}.json", slice_payload)

        self._atomic_write_json(session_dir / "meta.json", meta_payload)

    async def list_keys(self) -> list[str]:
        """List locally stored session ids."""
        if not self._base_dir.exists():
            return []
        if not self._base_dir.is_dir():
            raise ValueError(f"base_dir is not a directory: {self._base_dir}")

        session_ids: list[str] = []
        for entry in self._base_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                self._session_dir(entry.name)
            except ValueError:
                continue
            session_ids.append(entry.name)
        return sorted(session_ids)

    async def delete(self, key: str) -> bool:
        """Delete one locally stored session tree."""
        session_dir = self._session_dir(key)
        if not session_dir.exists():
            return False
        if not session_dir.is_dir():
            raise ValueError(f"session path is not a directory: {session_dir}")
        shutil.rmtree(session_dir)
        return True

    def _session_dir(self, key: str) -> Path:
        session_id = key.strip()
        if not session_id:
            raise ValueError("session key must not be empty")
        session_path = Path(session_id)
        if (
            session_id in {".", ".."}
            or "/" in session_id
            or "\\" in session_id
            or ".." in session_id
            or session_path.is_absolute()
            or len(session_path.parts) != 1
        ):
            raise ValueError(f"invalid session key: {key!r}")
        return self._base_dir / session_id

    def _normalize_payload(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        raw_state = payload.get("state")
        if isinstance(raw_state, Mapping):
            return (
                self._coerce_state_mapping(raw_state),
                dict(payload.get("meta")) if isinstance(payload.get("meta"), Mapping) else {},
            )
        return self._coerce_state_mapping(payload), {}

    def _coerce_state_mapping(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        state_payload: dict[str, dict[str, Any]] = {}
        for name, slice_payload in payload.items():
            if not isinstance(name, str) or not isinstance(slice_payload, Mapping):
                continue
            state_payload[name] = dict(slice_payload)
        return state_payload

    def _read_json_file(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise ValueError(f"json payload must be an object: {path}")
        return dict(data)

    def _atomic_write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(
                dict(payload),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.flush()
        os.replace(tmp_path, path)
