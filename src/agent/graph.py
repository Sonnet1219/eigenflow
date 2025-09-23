"""Multi-agent supervisor workflow using prebuilt components from LangGraph with main graph + subgraph architecture"""

import os
import uuid
import json
import logging
from datetime import datetime
from typing import Annotated, Any, Iterable

import psycopg
from langchain_openai import ChatOpenAI
from langgraph_supervisor import create_supervisor
from langgraph_supervisor.handoff import create_forward_message_tool
from langgraph.graph import StateGraph, MessagesState, START, END
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda

from src.agent.state import OverallState
from src.agent.schemas import IntentContext, OrchestratorScope, OrchestratorInputs
from src.agent.schemas import IntentClassification
from src.agent.prompts import (
    AI_RESPONDER_PROMPT,
    SUPERVISOR_PROMPT,
    INTENT_CLASSIFICATION_PROMPT
)
from src.agent.data_gateway import get_lp_mapping_string
from src.agent.configuration import Configuration
from src.agent.margin_tools import get_lp_margin_check
from src.db.database import DatabaseManager


logger = logging.getLogger(__name__)


LOW_CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "0.98"))


RECOVERABLE_SQLSTATES = {"57P01", "57P02", "57P03", "08003", "08006"}


def _is_recoverable_db_error(error: Exception) -> bool:
    """Identify database errors that can potentially be recovered automatically."""

    if isinstance(error, psycopg.Error):
        sqlstate = getattr(error, "sqlstate", None)
        if sqlstate and sqlstate in RECOVERABLE_SQLSTATES:
            return True
        message = str(error).lower()
        if "db_termination" in message or "connection is lost" in message:
            return True

    message = str(error).lower()
    return "db_termination" in message or "connection is lost" in message


async def _attempt_db_recovery() -> bool:
    """Force a database pool reconnect; return True on success."""

    try:
        await DatabaseManager.force_reconnect()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Database reconnection attempt failed: %s", exc, exc_info=True)
        return False


def _normalize_report_text(value: Any) -> str | None:
    """Convert mixed message payloads into a clean report string."""
    if value is None:
        return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        lowered = stripped.lower()
        if lowered.startswith("successfully transferred to ") or lowered in {"ok", "done", "success"}:
            return None
        return stripped or None

    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):  # fall back to repr if not JSON serializable
            text = str(value)
        text = text.strip()
        return text or None

    text = str(value).strip()
    return text or None


def _extract_latest_ai_report(messages: Iterable[Any]) -> str | None:
    """Walk the conversation backwards to find the latest AI/tool content."""
    if not messages:
        return None

    for msg in reversed(list(messages)):
        msg_type = getattr(msg, "type", getattr(msg, "role", None))
        content = getattr(msg, "content", msg)

        # Accept AI/assistant messages first
        if isinstance(msg, AIMessage) or (msg_type and msg_type in {"ai", "assistant"}):
            normalized = _normalize_report_text(content)
            if normalized:
                return normalized

        # Tool outputs sometimes contain the structured report string
        if msg_type == "tool":
            normalized = _normalize_report_text(content)
            if normalized:
                return normalized

    return None


def _looks_structured_report(text: str) -> bool:
    """Heuristically determine if the report is structured JSON-like data."""
    sample = text.strip()
    if not sample:
        return False
    if sample[0] in "[{" and sample[-1] in "]}" and ":" in sample:
        return True
    # Fallback: high ratio of punctuation vs letters implies structured content
    letters = sum(ch.isalpha() for ch in sample)
    punctuation = sum(ch in "{}[]:,\"" for ch in sample)
    return punctuation > letters


async def _render_text_report(raw_payload: str) -> str | None:
    """Use the AI responder prompt to turn structured payloads into narrative text."""
    if not raw_payload:
        return None

    try:
        conversation = [
            SystemMessage(content=AI_RESPONDER_PROMPT),
            HumanMessage(
                content=(
                    "请基于以下结构化数据生成完整的保证金与风险分析报告，"
                    "必须包含总体风险判断、关键风险点、趋势洞察，以及可执行建议。\n"
                    "数据如下（JSON 格式）：\n"
                    f"```json\n{raw_payload}\n```"
                )
            ),
        ]
        response = await model.ainvoke(conversation)
        if isinstance(response, AIMessage):
            rendered = (response.content or "").strip()
        else:
            rendered = str(response).strip()
        return rendered or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to render narrative report from structured payload: %s", exc, exc_info=True)
        return None


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
        existing_context = state.get("intentContext")
        skip_intent = state.get("skipIntentClassification")

        if skip_intent and existing_context:
            try:
                if isinstance(existing_context, IntentContext):
                    intent_context = existing_context
                else:
                    if hasattr(IntentContext, "model_validate"):
                        intent_context = IntentContext.model_validate(existing_context)
                    else:  # pydantic v1 fallback
                        intent_context = IntentContext(**existing_context)
            except Exception as validation_error:  # noqa: BLE001
                logger.warning("Failed to reuse provided intent context: %s", validation_error)
            else:
                return {"intentContext": intent_context, "skipIntentClassification": False}

        model = get_model()
        
        # Get the latest user message
        messages = state.get("messages", [])
        if not messages:
            # Return default intent context for empty messages
            default_context = IntentContext()
            return {"intentContext": default_context, "skipIntentClassification": False}
            
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

        lower_input = user_input.lower()

        # Auto-detect LP aliases if classifier missed them
        if not slots.get("lp"):
            if "cfh" in lower_input or "majestic" in lower_input:
                slots["lp"] = "[CFH] MAJESTIC FIN TRADE"
            elif "gbe" in lower_input or "global" in lower_input:
                slots["lp"] = "[GBEGlobal]GBEGlobal1"

        # Portfolio-wide keywords
        portfolio_keywords = ("一键", "全量", "全部", "全盘")
        if any(keyword in user_input for keyword in portfolio_keywords):
            slots["currentLevel"] = "portfolio"
            slots.pop("lp", None)

        # Rush / urgent keywords to help downstream scheduling
        rush_keywords = ("一键", "马上", "立即", "现在", "紧急", "urgent", "马上处理")
        if any(keyword in user_input for keyword in rush_keywords):
            slots["urgency"] = "rush"

        # Mark recheck loops for downstream prompts
        if "复查" in user_input or "recheck" in lower_input:
            slots["followup"] = "recheck"

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
        
        return {"intentContext": intent_context, "skipIntentClassification": False}
        
    except Exception as e:
        print(f"Error in intent classification: {e}")
        # Return default chat intent context on error
        default_context = IntentContext(intent="chat", confidence=0.5)
        return {"intentContext": default_context, "skipIntentClassification": False}


async def call_supervisor(state: OverallState, config: RunnableConfig) -> OverallState:
    """Call supervisor subgraph with transformed state and return updated messages."""
    try:
        # Extract intent context from state
        intent_context = state.get("intentContext", IntentContext())

        # Create structured orchestrator state based on intent context
        orchestrator_scope = OrchestratorScope(
            currentLevel=getattr(intent_context, 'slots', {}).get('currentLevel', 'lp'),
            brokerId=getattr(intent_context, 'slots', {}).get('brokerId'),
            lp=getattr(intent_context, 'slots', {}).get('lp'),
            group=getattr(intent_context, 'slots', {}).get('group')
        )

        slot_data = getattr(intent_context, 'slots', {}) or {}

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

        is_recheck = slot_data.get("followup") == "recheck"

        if slot_data.get("urgency") == "rush":
            options["urgency"] = "rush"
            options["slaSeconds"] = 15

        if is_recheck:
            options["context"] = "recheck"

        if orchestrator_scope.currentLevel == "portfolio":
            options["scanAll"] = True

        orchestrator_inputs = OrchestratorInputs(
            scope=orchestrator_scope,
            lps=lps,
            timepoint=None,
            options=options
        )

        conversation = state.get("messages", [])
        supervisor_messages_input = conversation

        if is_recheck and conversation:
            last_human_index = None
            for idx in range(len(conversation) - 1, -1, -1):
                candidate = conversation[idx]
                role = getattr(candidate, "type", getattr(candidate, "role", None))
                if isinstance(candidate, HumanMessage) or role in {"human", "user"}:
                    last_human_index = idx
                    break
            if last_human_index is not None:
                supervisor_messages_input = conversation[last_human_index:]

        # Use lp_margin_check tool for margin analysis
        tool_name = "lp_margin_check"

        # Create orchestrator state with messages for supervisor subgraph
        orchestrator_state = {
            "messages": supervisor_messages_input,
            "schemaVer": "dc/v1",
            "tool": tool_name,
            "inputs": orchestrator_inputs,
            "tenantId": None,
            "traceId": getattr(intent_context, 'traceId', str(uuid.uuid4())),
            "idempotencyKey": str(uuid.uuid4()),
            "occurredAt": getattr(intent_context, 'occurredAt', datetime.now().isoformat() + "Z"),
            "intentContext": intent_context
        }

        # Invoke supervisor subgraph with orchestrator state
        result = None
        for attempt in range(2):
            try:
                result = await supervisor_subgraph.ainvoke(orchestrator_state)
                break
            except Exception as exc:  # noqa: BLE001
                recoverable = _is_recoverable_db_error(exc)
                if attempt == 0 and recoverable:
                    logger.warning(
                        "Supervisor subgraph encountered recoverable DB error; attempting reconnect"
                    )
                    if await _attempt_db_recovery():
                        continue
                logger.error("Error calling supervisor subgraph: %s", exc, exc_info=True)
                raise

        if result is None:
            raise RuntimeError("Supervisor subgraph invocation failed after retries")

        supervisor_messages = result.get("messages", [])

        filtered_messages = []
        latest_report = None
        tool_payload = None
        for msg in supervisor_messages:
            msg_type = None
            content = ""

            if hasattr(msg, "type"):
                msg_type = getattr(msg, "type")
                content = getattr(msg, "content", "")
            elif hasattr(msg, "role"):
                msg_type = getattr(msg, "role")
                content = getattr(msg, "content", "")
            elif isinstance(msg, dict):
                msg_type = msg.get("type") or msg.get("role")
                content = msg.get("content", "")
            else:
                content = str(msg)

            normalized_content = _normalize_report_text(content)
            normalized_content = _normalize_report_text(content)

            if msg_type == "tool":
                if normalized_content:
                    tool_payload = normalized_content
                continue

            if normalized_content is None:
                continue

            if msg_type in {"ai", "assistant"} or isinstance(msg, AIMessage):
                filtered_messages.append(AIMessage(content=normalized_content))
                latest_report = normalized_content

        if not latest_report:
            latest_report = _extract_latest_ai_report(supervisor_messages)

        if not latest_report and tool_payload:
            latest_report = tool_payload

        if latest_report and _looks_structured_report(latest_report):
            rendered_report = await _render_text_report(tool_payload or latest_report)
            if rendered_report:
                latest_report = rendered_report
                filtered_messages.append(AIMessage(content=rendered_report))

        if not filtered_messages and latest_report:
            filtered_messages.append(AIMessage(content=latest_report))

        if not latest_report and not is_recheck:
            # Fall back to any previously stored report to keep conversation context
            latest_report = _normalize_report_text(state.get("latestReport"))

        if not filtered_messages and latest_report:
            filtered_messages.append(AIMessage(content=latest_report))

        if not filtered_messages and not latest_report:
            logger.warning(
                "Supervisor subgraph produced no assistant content for trace %s",
                orchestrator_state.get("traceId"),
            )

        if latest_report and slot_data.get("followup") == "recheck":
            if not latest_report.startswith("[RECHECK]"):
                timestamp = datetime.now().isoformat(timespec="seconds")
                annotated_report = f"[RECHECK][{timestamp}]\n{latest_report}"
                latest_report = annotated_report
                if filtered_messages and isinstance(filtered_messages[-1], AIMessage):
                    filtered_messages[-1] = AIMessage(content=annotated_report)
                else:
                    filtered_messages.append(AIMessage(content=annotated_report))

        updated_messages = state.get("messages", []) + filtered_messages

        return {
            "messages": updated_messages,
            "intentContext": intent_context,
            "latestReport": latest_report
        }

    except Exception as e:
        logger.error("Error calling supervisor subgraph: %s", e, exc_info=True)
        raise


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


transfer_to_ai_responder = create_handoff_tool(
    agent_name="ai_responder",
    description="Transfer to AI Responder for converting structured data to professional analysis text.",
)


def create_supervisor_subgraph():
    """Create supervisor subgraph with worker agents."""
    model = get_model()

    async def responder_fn(state: MessagesState, config: RunnableConfig) -> dict:
        conversation = [SystemMessage(content=AI_RESPONDER_PROMPT)]
        conversation.extend(state.get("messages", []))
        response = await model.ainvoke(conversation, config=config)
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(response))
        return {"messages": [response]}

    ai_responder = RunnableLambda(responder_fn)

    forwarding_tool = create_forward_message_tool("supervisor")

    supervisor = create_supervisor(
        [ai_responder],
        model=model,
        prompt=SUPERVISOR_PROMPT,
        add_handoff_messages=False,
        output_mode="last_message",
        tools=[forwarding_tool, get_lp_margin_check, transfer_to_ai_responder]
    )

    return supervisor.compile()


def human_approval_node(state: OverallState, config: RunnableConfig) -> Command:
    """Human approval node for margin check recommendations."""
    # Extract the last message which should contain the AI response
    conversation = state.get("messages") or []
    last_message = conversation[-1] if conversation else None
    configurable = Configuration.from_runnable_config(config)
    latest_report = _normalize_report_text(state.get("latestReport"))

    if not latest_report:
        latest_report = _extract_latest_ai_report(conversation)

    # Only interrupt for margin check reports, not regular chat
    intent_context = state.get("intentContext")
    if intent_context and intent_context.intent == "lp_margin_check_report":
        next_context = intent_context
        try:
            if isinstance(intent_context, IntentContext):
                next_context = (
                    intent_context.model_copy(deep=True)
                    if hasattr(intent_context, "model_copy")
                    else intent_context.copy(deep=True)
                )
            else:
                if hasattr(IntentContext, "model_validate"):
                    next_context = IntentContext.model_validate(intent_context)
                else:
                    next_context = IntentContext(**intent_context)
        except Exception as ctx_error:  # noqa: BLE001
            logger.warning("Failed to clone intent context for recheck: %s", ctx_error)
            next_context = intent_context

        user_input = interrupt({
            "type": "margin_check_approval",
            "report": latest_report
            or _normalize_report_text(getattr(last_message, "content", None))
            or "No report generated",
            "question": "Please review the margin analysis report above. You can:\n1. Enter feedback/comments to continue discussion\n2. Leave empty to end the session",
            "trace_id": intent_context.traceId,
            # card_id其实就是一个thread_id
            "card_id": configurable.thread_id
        })

        resume_text = str(user_input).strip() if user_input is not None else ""
        processed_resume = state.get("last_resume_message")

        # Ensure follow-up slot marks recheck for downstream prompts
        try:
            slots = getattr(next_context, "slots", {}) or {}
            if isinstance(slots, dict):
                slots["followup"] = "recheck"
                if hasattr(next_context, "slots"):
                    next_context.slots = slots
        except Exception as slot_error:  # noqa: BLE001
            logger.debug("Failed to annotate followup slot: %s", slot_error)

        # If user provides input we haven't processed in this cycle, restart from intent classification
        if resume_text and resume_text != processed_resume:
            follow_up = HumanMessage(content=resume_text)
            return Command(
                goto="classify_intent",
                update={
                    "messages": [follow_up],
                    "intentContext": next_context,
                    "last_resume_message": resume_text,
                    "latestReport": None,
                    "skipIntentClassification": True
                }
            )

        # If we already processed this input, clear the marker and finish
        if resume_text and resume_text == processed_resume:
            return Command(
                goto=END,
                update={
                    "last_resume_message": None,
                    "latestReport": latest_report,
                    "skipIntentClassification": False,
                },
            )

    # If no input or not a margin check, end the flow and reset resume marker
    return Command(
        goto=END,
        update={
            "last_resume_message": None,
            "latestReport": latest_report,
            "skipIntentClassification": False,
        },
    )


def create_main_graph():
    """Create main graph with intent classification, fast supervisor, and human approval."""
    # Create the main graph with OverallState for input
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
    
    recursion_limit = int(os.getenv("GRAPH_RECURSION_LIMIT", "40"))

    try:
        return graph.compile(
            checkpointer=checkpointer,
            store=store,
            recursion_limit=recursion_limit
        )
    except TypeError:
        logger.warning(
            "StateGraph.compile does not support recursion_limit on this version; proceeding with default limit."
        )
        return graph.compile(
            checkpointer=checkpointer,
            store=store
        )
