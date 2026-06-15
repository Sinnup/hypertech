
name: initial setup

description: I'm applying to a position as CTO of this company: https://www.hypertech.mx/. They actually need to change their way of working to be agentic, meaning all SDCL must be agentic, in order to implement operative efficiency and intelligence, for any kind of development and stack. 
Lets imagine we have 3 scenarios:
1. Creating a demo (fast and a POC, something. That doesn’t require much attention to detail, but rather a fast proposal or flow proposal.
2. Creating something to local, for internal purposes.
3. Creating to something that may go to production, in order to show functionality to a client.

## Special considerations
- For any scenario, there must be a support agent that has permissions for installing cli of specific vendors (e.g gh, aws, android, claude, etc cli), software an other required tools for development.
- There must be another agent that’s expert in documentation, regulations, laws, etc, that must be met, business allays agent relies o it and iterates over it.
- There’s also a lead agent, that acts as orchestrator between all parties, deciding also when to start from any point given an instruction or promo by a human in the loop, let’s say a human asks for changing something small in the UI, the it won’t start from gathering information, but rather share it to UX/UI agent. It must read human prompts and decide where to place or what to do with what’s asked. It will have permissions for everything in the project folder for local or device env, depending on configurations.
- There must be a changelog describing relevant changes, so that on every iteration, the LLM doesn’t start from scratch, but rather having previous context.
- There must a feature manager. The orchestrator also acts as feature manager, meaning there is a feature file, registering all features and changes by ticket and date.
- For this first instructions I’m giving to you, prioritize the usage of Claude. Let the orchestrator determine what model to use based on request, balancing how deep research and thinking must be made. For the simplest thins you can use Haiku and for something really complex then Opus.
- All api keys and stuff must be read as placeholders, there must be retrieval from vault or secrets manager. For simplicity of this first iteration, let’s just use a project.properties file or different ones, depending on context. Do not combine contexts.
- There will also be an agent expert on infrastructure that can containerize and orchestrate deployments based on docker, k8n, etc.
- The interaction between the agent and lead agent (orchestrator is quite relevant in order to determine a tech stack that will require a human in the loop to implement).
- The lead may ask anything to the human in the loop whenever required, do not hesitate to do it.

## Permissions, for this configuration, you set your local permissions to allow you to execute anything in bash, console, write, update or read documents, feel free to all of it.

## Proposed tech stack for orchestration:
- The best that matches with Claude, lets say lang change, lang graph, lang smith, etc, just provide me a good reason for the decision.
- It’s mandatory to have observability for open usage, stages, flow, outcomes, etc.
- I need to be able to watch the whole workflow from my phone, or at least the status and if something is required by the orchestrator, then asked through the phone as a notification. Using a third party app, web page, sms, or custom app.
- For git, by default use GitHub, with git actions, tickets manager, issues manager and all those things for features follow up.
- Later in the will share more agents to be created, because everyone will handle a specific platform, permissions, credentials etc. 
- Ask me anything, if you need data, ask.

For the 1st scenario, this may happen:
1. Create a POC, something easy type of MVC or the simpler, that only shows screens moving, dummy data, simpler walkthrough. This can be deployed to a sandbox in order to show it easily in about 10 minutes from starting the request.

For the 2nd and 3rd scenario, something like this may happen in the flow:
1. It starts by gathering information from a meeting with a client, meaning the transcript can be shared to an agent analyzer that determines the requirements more concisely and then
2. Share it to another business analyst agent (something like Rovo) that makes requirements about what's needed in the business context to implement according to policies and compliance of the necessary mexican regulations, for example EMV, LACPI, etc, taking also into consideration’s the latest projects and documents of the company.
3. If more documentation gets to be necessary, there must be a human in the loop in order to to provide links or attach documents.
4. After the business analyst gets deeper into compliance and minimum criteria to meet, 
5. Then an architect agent must create an High Level Design (it must check in tools, data sources or skills if something is preferred, based on community guidelines), in order to be shared again to the BA, analyze it again, then move forward or return it to architect if criteria is not met. A human in the loop must validate pre solution and approve or ask for another iteration.
6. After criteria is met, then it must be shared to a UX/UI designer that uses (Figma DevAI) so that it can then talk to BA about the design and if the design meets criteria.
7. There must be a human in the loop that validates proposal, if it succeeds then proceed, otherwise iterate with comments.
8. Then a coding agent (using Genius code or any other coding agent, depending on the stack it can implement Gemini, DeepSeek, or Claude as LLM Transformer) takes requirements from architect and designer and build the solution, using the assets from Figma DevAI (if applies) for front end or can just start coding backend or doing whatever it needs.
9. The coding agent must have a context about versioning (versioning must be a tool for any type of agentic developer).
10. Tools must be available for any Agent, in order to know how things are done in the company to meet quality criteria, timelines and deadlines. Thus information must be centralized.
11. After the code is committed and compiled and briefly tested in a server or emulator, or local infrastructure, a Security and breach analyzer, with DevAnalyzer or a similar tool, then scans the whole code and decides what must be changed in order to meet security and compliance criteria also taken from centralized tools). This agent shares the findings to the developer agent in order to meet criteria.
12. This process from Security agent to dev repeats after all criteria is met
13. Then the QA agent creates with TestGen all necessary testing that the app must meet, in order to avoid destroying the app in future commits.
14. Once QA agent creates the tests, then it runs them and executes a tool that verifies coverage, which must be above 95%.
15. After everything is tested and validated, then can be some scenarios: deployment to local env, cert or production, depending on the specification from the beginning. This also well be validate by a human in the loop, in order to decide where to deploy or what to build.
16.  There must be a place to have a vault of secrets, per project, something standardized, nothing must be hardcoded in the markdown files, but only in local .properties files or in git remote repository, placed in secrets manager (it will depend on the stack, but take into consideration the most popular). If credentials are provided, a human in the loop must intervene.
17. If there is something that can enhance my proposal, share it to me and update across the whole proposal.
18. Does my request makes sense?
