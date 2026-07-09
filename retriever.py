from langchain_core.tools import retriever
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma

embedding_model = OllamaEmbeddings(
    model = "qwen3-embedding:4b"
)

vector_store = Chroma(
    persist_directory = "chrome_db",
    collection_name = "react_reference_docs",
    embedding_function = embedding_model    
)
retriever = vector_store.as_retriever(
    search_kwargs={"k":5}
)

def retrieve(query:str):
    return retriever.invoke(query)