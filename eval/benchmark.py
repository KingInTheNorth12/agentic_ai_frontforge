from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

BENCHMARK_DIR = Path(__file__).parent / "benchmarks"
E2E_SUITE_PATH = BENCHMARK_DIR / "e2e" / "suite.json"
RAG_SUITE_PATH = BENCHMARK_DIR / "rag" / "suite.json"


class E2EBenchmarkCase(BaseModel):
    id: str
    prompt: str
    expected_features: list[str] = Field(default_factory=list)
    forbidden_features: list[str] = Field(default_factory=list)
    expected_routes: list[str] = Field(default_factory=list)
    required_dependencies: list[str] = Field(default_factory=list)
    forbidden_dependencies: list[str] = Field(default_factory=list)
    styling: str = "CSS"
    difficulty: Literal["small", "medium", "complex"] = "small"
    tags: list[str] = Field(default_factory=list)
    expected_clarification_needed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class RAGBenchmarkCase(BaseModel):
    id: str
    query: str
    target_library: str
    target_version: str = "latest"
    target_major_version: str = "18"
    expected_paths: list[str] = Field(default_factory=list)
    expected_symbols: list[str] = Field(default_factory=list)
    accepted_alternatives: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def load_e2e_benchmark_suite(path: str | Path = E2E_SUITE_PATH) -> list[E2EBenchmarkCase]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"E2E Benchmark suite file not found at: {file_path}")
    raw_data = json.loads(file_path.read_text(encoding="utf-8"))
    cases = []
    for item in raw_data:
        cases.append(E2EBenchmarkCase.model_validate(item))
    return deepcopy(cases)


def load_rag_benchmark_suite(path: str | Path = RAG_SUITE_PATH) -> list[RAGBenchmarkCase]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"RAG Benchmark suite file not found at: {file_path}")
    raw_data = json.loads(file_path.read_text(encoding="utf-8"))
    cases = []
    for item in raw_data:
        cases.append(RAGBenchmarkCase.model_validate(item))
    return deepcopy(cases)
