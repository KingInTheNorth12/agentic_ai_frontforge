import clarification_agent, planner_agent, UI_Architect, component_agent
import os

def main():
    print("============================ WELCOME ============================")

    data_dict = clarification_agent.clarify()

    print("Making the plan...")
    while not os.path.isfile("planner.json"):
        planner_agent.main(data_dict)
    print("Successfully created the plan. Let's move to next task...")

    print("Creating the UI Blueprint...")
    UI_Architect.main()
    print("Successfully created the UI Blueprint. Let's move to next task...")

    print("Creating components...")
    component_agent.main()

    
if __name__ == "__main__":
    main()
    
