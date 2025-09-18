"""Multi-agent supervisor workflow using prebuilt components from LangGraph with main graph + subgraph architecture"""

import os
import uuid
from datetime import datetime
from typing import Annotated

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph_supervisor import create_supervisor
from langgraph_supervisor.handoff import create_forward_message_tool
from langgraph.graph import StateGraph, MessagesState, START, END
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from src.agent.state import OverallState, OrchestratorState
from src.agent.schemas import IntentContext, OrchestratorScope, OrchestratorInputs
from src.agent.schemas import IntentClassification, MarginCheckToolResponse
from src.agent.prompts import (
    AI_RESPONDER_PROMPT,
    SUPERVISOR_PROMPT,
    INTENT_CLASSIFICATION_PROMPT
)
from src.agent.data_gateway import get_lp_mapping_string
from src.agent.configuration import Configuration
from src.agent.utils import chat_response
from src.agent.margin_tools import get_lp_margin_check


LOW_CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "0.98"))


def get_model():
    """Get configured ChatOpenAI model instance."""
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "qwen-plus-latest"),
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=0.5
    )


async def classify_intent(state: OverallState, config: RunnableConfig) -> OverallState:
    """Classify user intent using LLM with enhanced structured output."""
    try:
        model = get_model()
        
        # Get the latest user message
        messages = state.get("messages", [])
        if not messages:
            # Return default intent context for empty messages
            default_context = IntentContext()
            return {"intentContext": default_context}
            
        latest_message = messages[-1]
        user_input = latest_message.content if hasattr(latest_message, 'content') else str(latest_message)
        
        # Use structured output for intent classification
        structured_model = model.with_structured_output(IntentClassification)
        
        # Use the predefined prompt from prompts.py with dynamic formatting
        formatted_prompt = INTENT_CLASSIFICATION_PROMPT.format(
            user_input=user_input,
            lp_mapping=get_lp_mapping_string()
        )
        
        result = await structured_model.ainvoke([HumanMessage(content=formatted_prompt)])
        
        slots = result.slots.dict() if result.slots else {}
        intent_value = result.intent

        if result.confidence < LOW_CONFIDENCE_THRESHOLD:
            slots = {**slots, "fallback": True, "proposedIntent": result.intent}
            intent_value = "chat"
        
        # Convert Pydantic model to IntentContext dataclass
        intent_context = IntentContext(
            schemaVer=result.schemaVer,
            intent=intent_value,
            confidence=result.confidence,
            slots=slots,
            traceId=result.traceId,
            occurredAt=result.occurredAt
        )
        
        return {"intentContext": intent_context}
        
    except Exception as e:
        print(f"Error in intent classification: {e}")
        # Return default chat intent context on error
        default_context = IntentContext(intent="chat", confidence=0.5)
        return {"intentContext": default_context}


async def call_supervisor(state: OverallState, config: RunnableConfig) -> OverallState:
    """Call supervisor subgraph with transformed state and return updated messages."""
    try:
        # Extract intent context from state
        intent_context = state.get("intentContext", IntentContext())

        # intent_context_payload = None
        
        intent_context_payload = intent_context
        
        # Create structured orchestrator state based on intent context
        orchestrator_scope = OrchestratorScope(
            currentLevel=getattr(intent_context, 'slots', {}).get('currentLevel', 'lp'),
            brokerId=getattr(intent_context, 'slots', {}).get('brokerId'),
            lp=getattr(intent_context, 'slots', {}).get('lp'),
            group=getattr(intent_context, 'slots', {}).get('group')
        )
        
        # Extract LP list from context if available
        lps = []
        if orchestrator_scope.lp:
            lps.append(orchestrator_scope.lp)
        
        # Set default options for margin check operations
        options = {
            "requirePositionsDepth": True,
            "detectCrossPositions": True,
            "requireExplain": True
        }
        
        orchestrator_inputs = OrchestratorInputs(
            scope=orchestrator_scope,
            lps=lps,
            timepoint=None,  # Can be populated from user input if needed
            options=options
        )
        
        # Use lp_margin_check tool for margin analysis
        tool_name = "lp_margin_check"
        
        # Create orchestrator state with messages for supervisor subgraph
        orchestrator_state = {
            "messages": state.get("messages", []),
            "schemaVer": "dc/v1",
            "tool": tool_name,
            "inputs": orchestrator_inputs,
            "tenantId": None,  # Can be populated from environment or user context
            "traceId": getattr(intent_context, 'traceId', str(uuid.uuid4())),
            "idempotencyKey": str(uuid.uuid4()),
            "occurredAt": getattr(intent_context, 'occurredAt', datetime.now().isoformat() + "Z"),
            "intentContext": intent_context_payload
        }
        
        # Invoke supervisor subgraph with orchestrator state
        result = await supervisor_subgraph.ainvoke(orchestrator_state)
        
        # Extract messages from supervisor result
        supervisor_messages = result.get("messages", [])
        
        # Return updated state - add_messages annotation will handle merging automatically
        return {
            "messages": supervisor_messages,
            "intentContext": intent_context
        }
        
    except Exception as e:
        print(f"Error calling supervisor subgraph: {e}")
        # Return original state on error
        return state


# Create handoff tools for supervisor communication
def create_handoff_tool(*, agent_name: str, description: str | None = None):
    """Create handoff tool for supervisor to delegate tasks to worker agents."""
    name = f"transfer_to_{agent_name}"
    description = description or f"Transfer task to {agent_name}."

    @tool(name, description=description)
    def handoff_tool(
        state: Annotated[MessagesState, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        tool_message = {
            "role": "tool",
            "content": f"Successfully transferred to {agent_name}",
            "name": name,
            "tool_call_id": tool_call_id,
        }
        return Command(
            goto=agent_name,
            update={**state, "messages": state["messages"] + [tool_message]},
            graph=Command.PARENT,
        )

    return handoff_tool


# Create handoff tools
transfer_to_ai_responder = create_handoff_tool(
    agent_name="ai_responder",
    description="Transfer to AI Responder for converting structured data to professional analysis text.",
)

# Margin assistant removed - supervisor handles margin checks directly with get_lp_margin_report tool


# Worker agents and supervisor subgraph creation
def create_supervisor_subgraph():
    """Create supervisor subgraph with worker agents."""
    model = get_model()
    
    # Create worker agents using create_react_agent
    ai_responder = create_react_agent(
        model=model,
        tools=[chat_response],
        prompt=AI_RESPONDER_PROMPT,
        name="ai_responder"
    )

    # Removed margin_assistant - supervisor now handles margin checks directly

    forwarding_tool = create_forward_message_tool("supervisor")

    # Create supervisor subgraph and compile it
    supervisor = create_supervisor(
        [ai_responder],
        model=model,
        prompt=SUPERVISOR_PROMPT,
        add_handoff_messages=False,   # Don't add handoff messages to conversation history
        output_mode="last_message",   # Return only the last message from the active agent
        tools=[forwarding_tool, get_lp_margin_check]  # Supervisor can use tools directly
    )
    
    # Compile the supervisor subgraph so it has ainvoke method
    return supervisor.compile()


def human_approval_node(state: OverallState, config: RunnableConfig) -> Command:
    """Human approval node for margin check recommendations."""
    # Extract the last message which should contain the AI response
    last_message = state["messages"][-1] if state["messages"] else None
    configurable = Configuration.from_runnable_config(config)
    
    # Only interrupt for margin check reports, not regular chat
    intent_context = state.get("intentContext")
    if intent_context and intent_context.intent == "lp_margin_check_report":
        user_input = interrupt({
            "type": "margin_check_approval",
            "report": last_message.content if last_message else "No report generated",
            "question": "Please review the margin analysis report above. You can:\n1. Enter feedback/comments to continue discussion\n2. Leave empty to end the session",
            "trace_id": intent_context.traceId,
            "card_id": configurable.thread_id
        })
        
        # If user provides input, go back to supervisor for further discussion
        if user_input and str(user_input).strip():
            return Command(
                goto="call_supervisor",
                update={"messages": state["messages"] + [HumanMessage(content=str(user_input))]}
            )
    
    # If no input or not a margin check, end the flow
    return Command(goto=END)


def create_main_graph():
    """Create main graph with intent classification, supervisor subgraph, and human approval."""
    # Create the main graph with OverallState for input, OrchestratorState for output
    builder = StateGraph(OverallState)
    
    # Add intent classification node
    builder.add_node("classify_intent", classify_intent)
    
    # Add supervisor subgraph call node  
    builder.add_node("call_supervisor", call_supervisor)
    
    # Add human approval node for margin check reports
    builder.add_node("human_approval", human_approval_node)
    
    # Define the flow: START -> classify_intent -> call_supervisor -> human_approval -> END
    builder.add_edge(START, "classify_intent")
    builder.add_edge("classify_intent", "call_supervisor")
    builder.add_edge("call_supervisor", "human_approval")
    # human_approval uses Command to conditionally go to END or back to call_supervisor
    
    return builder


# Initialize components when module loads
model = get_model()
supervisor_subgraph = create_supervisor_subgraph()
graph = create_main_graph()


async def build_graph(checkpointer, store=None):
    """
    Compiles the graph with the given checkpointer and memory store.
    
    Args:
        checkpointer: AsyncPostgresSaver instance for checkpointing
        store: AsyncPostgresStore instance for memory storage (optional)
    
    Returns:
        Compiled graph instance
    """
    
    return graph.compile(
        checkpointer=checkpointer,
        store=store
    )
