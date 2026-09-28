from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

import config as app_config


class CLIConfig(BaseModel):
    """Configuration model for the CLI application."""
    model: str = 'qwen3:latest'
    embedding_model: str = 'qwen3-embedding:4b'
    rag_collection: str = 'frontend_reference_docs'
    rag_k: int = 4
    workspace_root: str = '.'
    max_repair_attempts: int = 2
    command_timeout: int = 180
    deliberation_config: str = "AUTO"
    allow_npm_scripts: bool = False
    verbosity: Literal['normal', 'verbose', 'debug'] = 'normal'
    color: Literal['auto', 'always', 'never'] = 'auto'
    json_output: bool = False
    quiet: bool = False
    default_project_dir: str | None = None



def load_config(**cli_kwargs: Any) -> CLIConfig:
    """
    Load and merge configuration from multiple sources.
    Precedence: CLI kwargs > env vars > project config > user config > defaults
    """
    merged_data: dict[str, Any] = {}

    # 1. User config (~/.config/frontend-agent/config.toml)
    user_config_path = Path.home() / '.config' / 'frontend-agent' / 'config.toml'
    if user_config_path.is_file():
        try:
            with open(user_config_path, "rb") as f:
                user_data = tomllib.load(f)
                merged_data.update(user_data)
        except Exception:
            pass

    # 2. Project config (.frontend-agent.toml in cwd)
    project_config_path = Path.cwd() / '.frontend-agent.toml'
    if project_config_path.is_file():
        try:
            with open(project_config_path, "rb") as f:
                project_data = tomllib.load(f)
                merged_data.update(project_data)
        except Exception:
            pass

    # 3. Environment variables
    if 'FRONTEND_AGENT_MODEL' in os.environ:
        merged_data['model'] = os.environ['FRONTEND_AGENT_MODEL']
    if 'FRONTEND_AGENT_EMBEDDING_MODEL' in os.environ:
        merged_data['embedding_model'] = os.environ['FRONTEND_AGENT_EMBEDDING_MODEL']
    if 'FRONTEND_AGENT_RAG_COLLECTION' in os.environ:
        merged_data['rag_collection'] = os.environ['FRONTEND_AGENT_RAG_COLLECTION']
    if 'FRONTEND_AGENT_WORKSPACE' in os.environ:
        merged_data['workspace_root'] = os.environ['FRONTEND_AGENT_WORKSPACE']
    if 'FRONTEND_AGENT_MAX_REPAIRS' in os.environ:
        try:
            merged_data['max_repair_attempts'] = int(os.environ['FRONTEND_AGENT_MAX_REPAIRS'])
        except ValueError:
            pass
    if 'FRONTEND_AGENT_COMMAND_TIMEOUT' in os.environ:
        try:
            merged_data['command_timeout'] = int(os.environ['FRONTEND_AGENT_COMMAND_TIMEOUT'])
        except ValueError:
            pass
    if 'FRONTEND_AGENT_ALLOW_NPM_SCRIPTS' in os.environ:
        merged_data['allow_npm_scripts'] = os.environ['FRONTEND_AGENT_ALLOW_NPM_SCRIPTS'] == '1'
        
    if 'NO_COLOR' in os.environ:
        merged_data['color'] = 'never'

    # 4. CLI kwargs (ignore None values)
    for k, v in cli_kwargs.items():
        if v is not None:
            merged_data[k] = v

    return CLIConfig(**merged_data)


def resolve_color(config: CLIConfig, is_tty: bool) -> bool:
    """Resolve whether color output should be enabled based on config and tty."""
    if config.color == 'always':
        return True
    if config.color == 'never':
        return False
    return is_tty


def apply_to_env(config: CLIConfig) -> None:
    """Apply CLI configuration to the main app config module state."""
    app_config.MODEL_NAME = config.model
    app_config.EMBEDDING_MODEL = config.embedding_model
    app_config.RAG_COLLECTION = config.rag_collection
    
    app_config.WORKSPACE_ROOT = Path(config.workspace_root).resolve()
    
    # Update derived paths in app_config
    app_config.AGENT_DIR = app_config.WORKSPACE_ROOT / '.frontend_agent'
    app_config.CHECKPOINT_DB = app_config.AGENT_DIR / 'checkpoints.sqlite'
    app_config.STORE_DB = app_config.AGENT_DIR / 'store.sqlite'
    app_config.CHROMA_DIR = app_config.AGENT_DIR / 'chroma'
    
    app_config.MAX_REPAIR_ATTEMPTS = config.max_repair_attempts
    app_config.COMMAND_TIMEOUT_SECONDS = config.command_timeout
    app_config.ALLOW_NPM_INSTALL_SCRIPTS = config.allow_npm_scripts
