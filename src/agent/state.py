
from __future__ import annotations
from typing_extensions import TypedDict, Annotated
from langgraph.graph import add_messages, MessagesState
from langchain_core.messages import AnyMessage
from typing import List, Optional, Dict, Any
import operator
from src.agent.schemas import IntentContext, OrchestratorInputs

# main graph state
class OverallState(TypedDict, total=False):
    """Main graph state managing overall workflow with enhanced intent classification."""
    messages: Annotated[list[AnyMessage], add_messages]
    intentContext: Optional[IntentContext]  # Enhanced intent context with full classification details
    # clarificationAttempts: Optional[int]  # Track how many clarification loops have been attempted


# subgraph state
class OrchestratorState(TypedDict, total=False):
    """State structure for orchestrator operations between main graph and supervisor subgraph."""
    messages: Annotated[list[AnyMessage], add_messages]  # Keep messages for LangGraph compatibility
    schemaVer: str  # Schema version identifier
    tool: str  # Tool identifier
    inputs: "OrchestratorInputs"  # Operation inputs
    tenantId: Optional[str]  # Tenant identifier
    traceId: str  # Unique trace identifier
    idempotencyKey: str  # Idempotency key for operation
    occurredAt: str  # ISO8601 timestamp of occurrence
    intentContext: Optional[IntentContext]  # Classified intent details for supervisor routing
