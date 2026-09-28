from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

import config as app_config


def ingest(paths: list[str], library: str, version: str | None = None) -> int:
    documents: list[Document] = []
    for raw in paths:
        root = Path(raw).resolve()
        if not root.exists():
            raise FileNotFoundError(root)
        for file in root.rglob("*"):
            if file.suffix.lower() not in {".md", ".mdx", ".txt"} or not file.is_file():
                continue
            documents.append(Document(
                page_content=file.read_text(encoding="utf-8", errors="ignore"),
                metadata={"source": str(file), "library": library, "version": version or "unknown", "major_version": version.split(".")[0] if version else "unknown"},
            ))

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=120)
    chunks = splitter.split_documents(documents)
    if not chunks:
        return 0

    store = Chroma(
        persist_directory=str(app_config.CHROMA_DIR),
        collection_name=app_config.RAG_COLLECTION,
        embedding_function=OllamaEmbeddings(model=app_config.EMBEDDING_MODEL),
    )
    ids = [hashlib.sha256((doc.metadata["source"] + library + (version or "unknown") + str(i) + doc.page_content).encode()).hexdigest() for i, doc in enumerate(chunks)]
    store.add_documents(chunks, ids=ids)
    # Newly indexed documents must be visible to repeated queries.
    cache_dir = app_config.AGENT_DIR / "cache" / "rag"
    for cached in cache_dir.glob("*.json"):
        cached.unlink(missing_ok=True)
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--library", required=True, help="Metadata label such as react, vite, tailwind")
    parser.add_argument("--version", help="Documentation version, required for use during generation")
    args = parser.parse_args()
    count = ingest(args.paths, args.library, args.version)
    print(f"Ingested {count} chunks into {app_config.RAG_COLLECTION}")


if __name__ == "__main__":
    main()
