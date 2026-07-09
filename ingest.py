import os
import sys
from pathlib import Path
from langchain_ollama import OllamaEmbeddings
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

if(len(sys.argv)<2):
    print("Provide at least one path")
    sys.exit(1)

target_folders = sys.argv[1:]
tasks = []

print(f"--- Configure Context ---\n\n")

for folder in target_folders:
    clean_path = Path(folder)

    folder_name = clean_path.name if clean_path.name else clean_path.parent.name

    print(f"Target Folder: {folder_name}")
    context = input("What is library/framework is this?: ").strip()

    if not context:
        context = f"{folder_name.split("_").join(" ")}"
    
    tasks.append({
        "path": clean_path,
        "name": folder_name,
        "context": context
    })

print("--- Initialising embedding ---")

embedding_model = OllamaEmbeddings(model="qwen3-embedding:4b")

for task in tasks:
    folder_path = task["path"]
    folder_name = task["name"]
    context_str = task["context"]

    print(f"Processing {folder_name}...")
    print(f"Context: {context_str}")
    
    raw_documents = []

    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.endswith((".md",".mdx")): # to find the documentation
                file_path = os.path.join(root,file)
                with open(file_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
                doc = Document(page_content=text_content,
                    metadata = {"source": file_path}) 
                raw_documents.append(doc)

    print(f"Loaded {len(raw_documents)} documentation files.")

    text_splitter = RecursiveCharacterTextSplitter(chunk_size = 750, chunk_overlap = 50)
    chunks = text_splitter.split_documents(raw_documents) # <- this takes a list of Document object
    total_chunks = len(chunks)
    print(f"Split data in {total_chunks} chunks")

    for chunk in chunks:
        chunk.page_content = f"Instruct: Store this {context_str} snippet for code generation architecture pipeline\nQuery: {chunk.page_content}" # why???


    print("Starting embedding process... this may take time :(:( ")

    vector_store = Chroma(embedding_function=embedding_model,
                        persist_directory="./chrome_db",
                        collection_name=folder_name)

    BATCH_SIZE = 20
    print(f"Feeding chunks of {BATCH_SIZE}...")

    for i in range(0,total_chunks,BATCH_SIZE):
        batch = chunks[i:i+BATCH_SIZE]

        vector_store.add_documents(batch)

        completed = min(i+BATCH_SIZE,total_chunks)
        print(f"Completed {completed}/{total_chunks}")
        

    print(f"Success!! Completed ingesting {folder_name}")