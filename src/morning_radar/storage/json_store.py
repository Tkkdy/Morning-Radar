"""Small, explicit JSON persistence with atomic replacement."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter


def write_json(path: Path, data: Any) -> None:
    """Write UTF-8 JSON atomically so interrupted runs do not leave partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def save_model(path: Path, model: BaseModel) -> None:
    write_json(path, model.model_dump(mode="json"))


def load_model[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(read_json(path))


def save_models(path: Path, models: list[BaseModel]) -> None:
    write_json(path, [model.model_dump(mode="json") for model in models])


def load_models[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> list[ModelT]:
    return TypeAdapter(list[model_type]).validate_python(read_json(path))
