import os
from pathlib import Path

__version__ = "0.3.0"

MODEL_NAME = os.getenv("FRONTEND_AGENT_MODEL", "qwen3:latest")

EMBEDDING_MODEL = os.getenv("FRONTEND_AGENT_EMBEDDING_MODEL", "qwen3-embedding:4b")
RAG_COLLECTION = os.getenv("FRONTEND_AGENT_RAG_COLLECTION", "frontend_reference_docs")

WORKSPACE_ROOT = Path(os.getenv("FRONTEND_AGENT_WORKSPACE", ".")).resolve()
AGENT_DIR = WORKSPACE_ROOT / ".frontend_agent"
CHECKPOINT_DB = AGENT_DIR / "checkpoints.sqlite"
STORE_DB = AGENT_DIR / "store.sqlite"
CHROMA_DIR = AGENT_DIR / "chroma"

MAX_REPAIR_ATTEMPTS = int(os.getenv("FRONTEND_AGENT_MAX_REPAIRS", "2"))
COMMAND_TIMEOUT_SECONDS = int(os.getenv("FRONTEND_AGENT_COMMAND_TIMEOUT", "180"))
DELIBERATION_CONFIG = os.getenv("FRONTEND_AGENT_DELIBERATION", "AUTO")

# Package lifecycle scripts can execute arbitrary code. Keep them disabled by
# default until the generated project runs inside a real sandbox.
ALLOW_NPM_INSTALL_SCRIPTS = os.getenv("FRONTEND_AGENT_ALLOW_NPM_SCRIPTS", "0") == "1"
