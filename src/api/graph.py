"""API endpoints for chat operations using multi-agent supervisor."""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from langgraph.types import Command
import json
import asyncio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


class EventInput(BaseModel):
    """Event input for margin check operations."""
    messages: Optional[List[Dict[str, Any]]] = Field(default=None, description="Optional messages list")
    thread_id: Optional[str] = Field(default=None, description="Thread ID for conversation continuity")
    eventType: Optional[str] = Field(default=None, description="Event type for automated alerts")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Event payload data")


class HistoryInput(BaseModel):
    """Input for history retrieval operations."""
    thread_id: str = Field(description="Thread ID to retrieve history for")


class MarginCheckResponse(BaseModel):
    """Response for margin check operations."""
    status: str = Field(description="Status of the operation")
    report: Optional[str] = Field(default=None, description="Generated margin analysis report")
    recommendations: Optional[str] = Field(default=None, description="Generated recommendations")
    interrupt_data: Optional[Dict[str, Any]] = Field(default=None, description="Interrupt data if human approval needed")
    thread_id: str = Field(description="Thread ID for this conversation")


@router.post("/margin-check")
async def margin_check_endpoint(request: Request, body: EventInput):
    """Execute margin check analysis with human-in-the-loop approval."""
    try:
        graph = request.app.state.graph
        
        # Check if this is a MARGIN_ALERT event - skip intent classification
        if body.eventType == "MARGIN_ALERT" and body.payload:
            # Create alert message for direct processing
            lp_name = body.payload.get("lp", "Unknown")
            margin_level = body.payload.get("marginLevel", 0) * 100  # Convert to percentage
            threshold = body.payload.get("threshold", 0.8) * 100
            
            alert_message = f"MARGIN ALERT: {lp_name} has reached {margin_level:.1f}% margin utilization (threshold: {threshold:.0f}%). Generate immediate margin analysis and recommendations."
            messages = [HumanMessage(content=alert_message)]
            
            # Set intent context to skip classification
            from src.agent.schemas import IntentContext
            intent_context = IntentContext(
                intent="lp_margin_check_report",
                confidence=1.0,
                slots={"lp": lp_name},  # Pass lp_name for direct filtering
                traceId=f"alert_{lp_name}_{int(datetime.now().timestamp())}"
            )
            
            initial_state = {
                "messages": messages,
                "intentContext": intent_context
            }
        else:
            # Regular message processing
            if body.messages:
                # Convert dict messages to HumanMessage objects
                messages = []
                for msg in body.messages:
                    if msg.get("type") == "human" or msg.get("role") == "user":
                        messages.append(HumanMessage(content=msg.get("content", "")))
            else:
                # Default margin check request
                messages = [HumanMessage(content="请生成当前LP账户的保证金水平报告和建议")]
            
            initial_state = {
                "messages": messages
            }
        
        # Use provided thread_id or generate new one
        thread_id = body.thread_id or f"margin_check_{hash(str(messages))}"
        config = {"configurable": {"thread_id": thread_id}}
        
        # Use ainvoke for simpler implementation
        try:
            logger.info(f"Invoking graph with initial_state: {initial_state}")
            result = await graph.ainvoke(initial_state, config=config)
            
            # Check if there's an interrupt
            if "__interrupt__" in result:
                interrupt_info = result["__interrupt__"][0] if result["__interrupt__"] else None
                
                response = {
                    "type": "interrupt",
                    "status": "awaiting_approval",
                    "interrupt_data": interrupt_info.value if interrupt_info else None,
                    "thread_id": thread_id
                }
                return response
            
            # Extract final message content
            final_content = ""
            if "messages" in result and result["messages"]:
                final_content = result["messages"][-1].content if hasattr(result["messages"][-1], 'content') else ""
            
            response = {
                "type": "complete",
                "status": "completed",
                "content": final_content,
                "thread_id": thread_id
            }
            return response
            
        except Exception as e:
            logger.error(f"Error in graph execution: {e}")
            return {
                "type": "error",
                "status": "error",
                "error": str(e),
                "thread_id": thread_id
            }
        
        
    except Exception as e:
        logger.error(f"Margin check endpoint error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/margin-check/recheck")
async def margin_recheck_endpoint(request: Request, body: EventInput):
    """Resume margin check conversation after human approval."""
    try:
        graph = request.app.state.graph
        
        if not body.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required for recheck")
        
        config = {"configurable": {"thread_id": body.thread_id}}
        
        # Determine user input for resuming
        user_input = ""
        if body.messages:
            # Extract user input from messages
            for msg in body.messages:
                if msg.get("type") == "human" or msg.get("role") == "user":
                    user_input = msg.get("content", "")
                    break
        
        # Use ainvoke for simpler implementation
        try:
            result = await graph.ainvoke(Command(resume=user_input), config=config)
            
            # Check if there's an interrupt
            if "__interrupt__" in result:
                interrupt_info = result["__interrupt__"][0] if result["__interrupt__"] else None
                
                response = {
                    "type": "interrupt",
                    "status": "awaiting_approval",
                    "interrupt_data": interrupt_info.value if interrupt_info else None,
                    "thread_id": body.thread_id
                }
                return response
            
            # Extract final message content
            final_content = ""
            if "messages" in result and result["messages"]:
                final_content = result["messages"][-1].content if hasattr(result["messages"][-1], 'content') else ""
            
            response = {
                "type": "complete",
                "status": "completed",
                "content": final_content,
                "thread_id": body.thread_id
            }
            return response
            
        except Exception as e:
            logger.error(f"Error in recheck execution: {e}")
            return {
                "type": "error",
                "status": "error",
                "error": str(e),
                "thread_id": body.thread_id
            }
        
        
    except Exception as e:
        logger.error(f"Margin recheck endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/margin-check/history")
async def margin_check_history_endpoint(request: Request, body: HistoryInput):
    """Retrieve execution history for a margin check thread."""
    try:
        graph = request.app.state.graph
        
        if not body.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")
        
        config = {"configurable": {"thread_id": body.thread_id}}
        
        # Get the checkpointer from the graph
        checkpointer = graph.checkpointer
        if not checkpointer:
            raise HTTPException(status_code=500, detail="Checkpointer not available")
        
        try:
            # Get checkpoint history using the proper async method
            history_steps = []
            
            # Use graph.aget_state_history() for async access to state history
            async for state_snapshot in graph.aget_state_history(config):
                step_data = {
                    "checkpoint_id": state_snapshot.config.get("configurable", {}).get("checkpoint_id"),
                    "step": state_snapshot.metadata.get("step", 0),
                    "source": state_snapshot.metadata.get("source", "unknown"),
                    "writes": state_snapshot.metadata.get("writes", {}),
                    "created_at": state_snapshot.created_at.isoformat() if state_snapshot.created_at and hasattr(state_snapshot.created_at, 'isoformat') else str(state_snapshot.created_at) if state_snapshot.created_at else None,
                    "values": {},
                    "next": list(state_snapshot.next) if state_snapshot.next else [],
                    "tasks": [],
                    "parent_config": state_snapshot.parent_config
                }
                
                # Extract state values
                if state_snapshot.values:
                    for key, value in state_snapshot.values.items():
                        if key == "messages" and value:
                            # Format messages for readability
                            messages_data = []
                            for msg in value:
                                if hasattr(msg, 'content') and hasattr(msg, 'type'):
                                    # Parse JSON content for tool messages
                                    content = msg.content
                                    parsed_content = None
                                    if msg.type == "tool" and content:
                                        try:
                                            parsed_content = json.loads(content)
                                        except (json.JSONDecodeError, TypeError):
                                            parsed_content = None
                                    
                                    msg_data = {
                                        "type": msg.type,
                                        "content": content[:300] + "..." if len(str(content)) > 300 else content,
                                        "parsed_content": parsed_content,
                                        "name": getattr(msg, 'name', None),
                                        "additional_kwargs": getattr(msg, 'additional_kwargs', {})
                                    }
                                    messages_data.append(msg_data)
                                else:
                                    messages_data.append(str(msg)[:300] + "..." if len(str(msg)) > 300 else str(msg))
                            step_data["values"]["messages"] = messages_data
                        elif key == "intentContext" and value:
                            # Parse intentContext string into structured data
                            try:
                                # Parse the intentContext string format
                                context_parts = {}
                                if isinstance(value, str):
                                    import re
                                    # Use regex to match key=value pairs, handling quoted values
                                    pattern = r"(\w+)=('[^']*'|\"[^\"]*\"|[^\s]+)"
                                    matches = re.findall(pattern, value)
                                    
                                    for k, v in matches:
                                        # Remove quotes
                                        v = v.strip("'\"")
                                        
                                        # Parse values
                                        if v == 'None':
                                            v = None
                                        elif v.lower() == 'true':
                                            v = True
                                        elif v.lower() == 'false':
                                            v = False
                                        elif v.replace('.', '').replace('-', '').isdigit():
                                            v = float(v) if '.' in v else int(v)
                                        elif v.startswith('{') and v.endswith('}'):
                                            try:
                                                v = json.loads(v.replace("'", '"'))
                                            except:
                                                pass
                                        
                                        context_parts[k] = v
                                
                                step_data["values"][key] = context_parts if context_parts else value
                            except Exception:
                                # Fallback to string if parsing fails
                                value_str = str(value)
                                step_data["values"][key] = value_str[:500] + "..." if len(value_str) > 500 else value_str
                        else:
                            # For other state values, truncate if too long
                            value_str = str(value)
                            step_data["values"][key] = value_str[:500] + "..." if len(value_str) > 500 else value_str

                # Extract task information with detailed interrupt data
                if state_snapshot.tasks:
                    for task in state_snapshot.tasks:
                        task_info = {
                            "id": task.id,
                            "name": task.name,
                            "error": str(task.error) if task.error else None,
                            "interrupts": []
                        }
                        
                        # Parse interrupt data for better readability
                        if task.interrupts:
                            for interrupt in task.interrupts:
                                # Handle different interrupt object types
                                if hasattr(interrupt, 'id') and hasattr(interrupt, 'value'):
                                    interrupt_data = {
                                        "id": interrupt.id,
                                        "value": interrupt.value
                                    }
                                elif isinstance(interrupt, dict):
                                    interrupt_data = {
                                        "id": interrupt.get("id"),
                                        "value": interrupt.get("value", {})
                                    }
                                else:
                                    # Fallback for unknown interrupt types
                                    interrupt_data = {
                                        "id": str(getattr(interrupt, 'id', 'unknown')),
                                        "value": str(interrupt)
                                    }
                                task_info["interrupts"].append(interrupt_data)
                        
                        step_data["tasks"].append(task_info)
                
                # Extract node information from writes
                if state_snapshot.metadata.get("writes"):
                    step_data["executed_nodes"] = list(state_snapshot.metadata["writes"].keys())
                else:
                    step_data["executed_nodes"] = []
                
                history_steps.append(step_data)
            
            # Sort by step number for chronological order (ascending - earliest first)
            history_steps.sort(key=lambda x: x["step"])
            
            # Calculate summary statistics
            total_steps = len(history_steps)
            completed_steps = sum(1 for step in history_steps if step["source"] != "input")
            pending_steps = total_steps - completed_steps
            
            # Get executed nodes from all steps
            all_executed_nodes = []
            for step in history_steps:
                if step.get("executed_nodes"):
                    all_executed_nodes.extend(step["executed_nodes"])
            
            # Get latest timestamp
            latest_timestamp = None
            if history_steps:
                latest_step = max(history_steps, key=lambda x: x["step"])
                latest_timestamp = latest_step.get("created_at")
            
            return {
                "thread_id": body.thread_id,
                "summary": {
                    "total_steps": total_steps,
                    "completed_steps": completed_steps,
                    "pending_steps": pending_steps,
                    "executed_nodes": list(set(all_executed_nodes)),
                    "latest_timestamp": latest_timestamp
                },
                "execution_history": history_steps,
                "status": "success"
            }
            
            
        except Exception as e:
            logger.error(f"Error retrieving checkpoint history: {e}")
            return {
                "thread_id": body.thread_id,
                "error": f"Failed to retrieve history: {str(e)}",
                "status": "error"
            }
        
    except Exception as e:
        logger.error(f"History endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")