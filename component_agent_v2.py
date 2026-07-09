import json, os, re, glob
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from langchain.tools import tool

# Locating files -> planner.json, ui_blueprint.json
def load_planner():
    '''To load planner.json'''
    if os.path.isfile("planner.json"):
        pass

def load_UI_blueprint():
    '''To load the UI blue print'''
    if os.path.isfile("ui_blueprint.json"):
        pass

# Parsing through the directory

# Opening files to write

# Writing
def write_code(extracted_response, out_dir, file_name):
    code =  extracted_response.strip()
    code = re.sub(r"(^.*?)```[a-zA-Z]*\n",'',code)
    code = re.sub(r"```(.*$)",'',code)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir,file_name)
    with open(out_path, "w", encoding= "utf-8") as f:
        f.write(code)
    print(f"\nCode saved to {out_path}")


llm = ChatOllama(model="qwen3:latest",
                temperature=0.4,
                validate_model_on_init=True)

