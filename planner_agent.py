from clarification_agent import clarify
from langchain.agents import create_agent
from langchain_ollama import ChatOllama
from langchain.tools import tool
import json

SYSTEM_PROMPT = ("Role: You are a Professional Web Development Expert"
                 "You will be given a structured dictionary"
                 "Your task is to decompose the given requirements into a structured plan, ready for industry grade production"
                 "CRITICAL REQUIREMENTS FOR THE PLAN \n" \
                 "1. FOLDER STRUCTURE: First define the project folder hierarchy explicitly, listing where each file would go.\n"
                 "2. DEPENDENCIES: Define the exact dependencies and packages needed under 'dependency' key\n"
                 "3. COMPONENT: Component details in explicit manner, listing out exact requirements\n"
                 "4. ROUTING STRUCTURE\n"
                 "Explicitly define all things which are needed in great detail"
                 "The final output would be in a JSON format"
                 "You will use the given tools to write the plan"
                 "In CURRENT WORKING DIRECTORY")

# @tool
# def os_tool(path: str) -> str:
#     """Use this tool to create directory"""
#     os.makedirs(path,exist_ok=True)
#     return f"Directory path created: {path}"

@tool
def json_tool(info: dict, mode: str) -> str:
    '''Use this tool to write the PROJECT PLAN as a JSON file
    info contains the information you want write to the file.
    mode is the mode in which you will open the JSON file. 
    "a" mode for append to end of file.
    '''
    try:
        with open("planner.json",mode,encoding="utf-8") as f:
            json.dump(info,f)
            return f"Success writing plan to plan.json in {mode} mode"
    except Exception as e:
        return f"Error writing JSON file {e}"



model = ChatOllama(
    model="qwen3:latest",
    temperature=0.3,
    validate_model_on_init=True
)

agent = create_agent(
    model=model,
    system_prompt= SYSTEM_PROMPT,
    tools= [json_tool]
)

def main(raw_data):
    user_input = json.dumps(raw_data)
    print("[System] Running planner agent... Writing planner.json.")
    agent.invoke({"messages": [{"role": "user", "content": user_input}]})
    print("[System] Done.")

