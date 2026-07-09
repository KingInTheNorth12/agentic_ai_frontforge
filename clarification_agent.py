from langchain.agents import create_agent
from langchain_ollama import ChatOllama
from pydantic import BaseModel,Field
import sys


class website(BaseModel):
    title:str=Field(description="Title8 of the Project (2-3 words)")
    purpose:str=Field(description="The purpose of the website")
    framework:str=Field(description="The tech-stack used in website")
    styling:str=Field(description="")
    theme:str=Field(description="Visual theme of the website")
    authentication:bool=Field(description="Whether authentication required to access")
    key_sections:str=Field(description="What key sections should be included in the website")
    responsive:bool=Field(description="Whether the site should be responsive")
    icons:bool=Field(description="Whether to include icons in the wbsite")
    other:str=Field(description="Any other specifications requested")

def clarify():
    
        system_prompt = ("""
    You are a clarification agent for a website-building assistant.
    
    Your task:
    - Read the user's project description.
    - Determine whether each of the following fields has been explicitly specified.
    
    Fields:
    1. title
    2. purpose
    3. framework
    4. styling
    5. theme
    6. authentication
    7. key_sections
    8. responsive
    9. icons
    
    Rules:
    - Never assume information that the user has not explicitly provided.
    - Ask questions ONLY for fields that are missing.
    - Do NOT ask about fields that are already answered.
    - Ask one concise question per missing field.
    - Return ONLY a Python list of strings.
    - Do not include explanations, numbering, or markdown.
    
    Example output:
    [
        "What frontend framework would you like to use?",
        "Should the website support user authentication?",
        "Would you like the site to be responsive on mobile devices?"
    ]
    """)
    
    
        system_prompt2=("""
            Input: You will be given some context.
            Task: From that you are supposed to generate structured output.
            
    """)
    
        model=  ChatOllama(
            model="qwen3:latest",
            temperature=0.3,
            validate_model_on_init=True,
            think=False
        )  
        questions_agent = create_agent(
            model = model,
            tools=[],
            system_prompt=system_prompt
            )   




        user_prompt = input("[SYSTEM]What do you want to build today?")
        resp = questions_agent.invoke({f"messages":[{"role":"user","content": user_prompt }]})
        question_list = resp["messages"][1].content.split("\"")
        del question_list[0::2] 
        curr_prompt = user_prompt
        for question in question_list:
            temp_answer = input(f"[SYSTEM] {question}")
            curr_prompt = curr_prompt + "\n" + "Question:" + question + "Answer:" + temp_answer

        last_question = "Any other specifications?"
        last_ans =  input(last_question)

        curr_prompt =  curr_prompt + "\n" + "Question:" + last_question + "Answer:" + last_ans

        model_with_structure = model.with_structured_output(website)
        resp = model_with_structure.invoke("Given the following context,elaborate.." + curr_prompt)
        keyword_list = ("title","purpose","framework","styling","theme","authentication","key_sections","responsive","icons","other")
        result = {}
        for keyword in keyword_list:
            result[keyword] = getattr(resp,keyword)
        return result

def main():
    res = clarify()
    print(res)

if __name__ == "__main__":
    main()


