"""Alert Service API endpoints."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List
from fastapi import APIRouter, BackgroundTasks
from langchain_core.messages import HumanMessage

# Import data gateway from main project
from src.agent.data_gateway import EigenFlowAPI
from src.agent.graph import graph as _margin_graph_builder
from src.agent.schemas import IntentContext

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:  # pragma: no cover - fallback if module path changes
    MemorySaver = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alert", tags=["alert"])

if hasattr(_margin_graph_builder, "ainvoke"):
    margin_analysis_graph = _margin_graph_builder
else:
    try:
        if hasattr(_margin_graph_builder, "compile"):
            kwargs = {"checkpointer": MemorySaver()} if MemorySaver else {}
            margin_analysis_graph = _margin_graph_builder.compile(**kwargs)
        else:
            raise AttributeError("Graph builder does not support compile()")
    except Exception as compile_error:
        logger.error("Failed to compile margin analysis graph: %s", compile_error)
        margin_analysis_graph = None

# Configuration
MONITORING_INTERVAL = 60  # seconds
MARGIN_THRESHOLD =   # percentage


class MonitoringService:
    """LP margin monitoring service."""
    
    def __init__(self):
        self.is_running = False
        self.api_client = EigenFlowAPI()
        self.last_alerts = {}  # Track last alert time for each LP
    
    async def monitor_margins(self):
        """Monitor LP margins and log alerts when thresholds are exceeded."""
        self.is_running = True
        logger.info("Starting LP margin monitoring service")
        
        while self.is_running:
            try:
                # Fetch LP account data
                accounts = await self.fetch_lp_data()

                alert_triggered = False
                healthy_lps = []
                
                for account in accounts:
                    lp_name = account.get("LP", "Unknown")
                    margin_level = account.get("Margin Utilization %", 0)  # Already in percentage
                    
                    # Check if margin level exceeds threshold
                    if margin_level > MARGIN_THRESHOLD:
                        # Check if we already logged an alert recently (avoid spam)
                        last_alert = self.last_alerts.get(lp_name)
                        now = datetime.now()

                        if not last_alert or (now - last_alert).seconds > MONITORING_INTERVAL:
                            self.last_alerts[lp_name] = now
                            logger.warning(f"🚨 MARGIN ALERT: {lp_name} margin level {margin_level:.2f}% exceeds threshold {MARGIN_THRESHOLD}%")
                            alert_triggered = True

                            try:
                                await self.trigger_margin_report(lp_name, margin_level)
                            except Exception as report_error:
                                logger.error(f"Failed to generate margin report for {lp_name}: {report_error}")
                    else:
                        healthy_lps.append(f"{lp_name} ({margin_level:.2f}%)")
                
                # Report healthy status if no alerts were triggered
                if not alert_triggered and healthy_lps:
                    logger.info(f"✅ All LPs healthy: {', '.join(healthy_lps)} - all below {MARGIN_THRESHOLD}% threshold")
                
                # Wait before next check
                await asyncio.sleep(MONITORING_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(MONITORING_INTERVAL)
    
    async def fetch_lp_data(self) -> List[Dict[str, Any]]:
        """Fetch LP account data from Data Gateway."""
        try:
            # Authenticate if needed
            if not self.api_client.access_token:
                auth_result = self.api_client.authenticate()
                if not auth_result.get("success"):
                    logger.error(f"Authentication failed: {auth_result.get('error')}")
                    return []
            
            # Fetch account data
            accounts_result = self.api_client.get_lp_accounts()
            return accounts_result if isinstance(accounts_result, list) else []
            
        except Exception as e:
            logger.error(f"Error fetching LP data: {e}")
            return []


    def stop_monitoring(self):
        """Stop the monitoring service."""
        self.is_running = False
        logger.info("LP margin monitoring service stopped")

    async def trigger_margin_report(self, lp_name: str, margin_level: float) -> None:
        """Trigger LangGraph margin report generation for the specified LP."""
        if margin_analysis_graph is None:
            logger.error("Margin analysis graph is not available; skipping report generation.")
            return

        alert_message = (
            f"MARGIN ALERT: {lp_name} has reached {margin_level:.2f}% margin utilization "
            f"(threshold: {MARGIN_THRESHOLD:.2f}%). Generate immediate margin analysis and recommendations."
        )

        intent_context = IntentContext(
            intent="lp_margin_check_report",
            confidence=1.0,
            slots={"lp": lp_name},
            traceId=f"alert_{lp_name}_{int(datetime.now().timestamp())}",
        )

        initial_state = {
            "messages": [HumanMessage(content=alert_message)],
            "intentContext": intent_context,
        }

        thread_id = f"margin_alert_{lp_name}_{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}

        result = await margin_analysis_graph.ainvoke(initial_state, config=config)

        if "__interrupt__" in result and result["__interrupt__"]:
            interrupt_info = result["__interrupt__"][0]
            interrupt_value = getattr(interrupt_info, "value", interrupt_info)
            logger.warning(
                "Margin report generation for %s requires follow-up: %s",
                lp_name,
                interrupt_value,
            )
            return

        final_content = ""
        if "messages" in result and result["messages"]:
            final_message = result["messages"][-1]
            final_content = getattr(final_message, "content", str(final_message))

        if final_content:
            preview = final_content if len(final_content) < 500 else final_content[:500] + "..."
            logger.info("📊 Margin report for %s (thread %s): %s", lp_name, thread_id, preview)
        else:
            logger.info("Margin report for %s completed with no content (thread %s).", lp_name, thread_id)


# Global monitoring service instance
monitoring_service = MonitoringService()


@router.post("/start-monitoring")
async def start_monitoring(background_tasks: BackgroundTasks):
    """Start the LP margin monitoring service."""
    if not monitoring_service.is_running:
        background_tasks.add_task(monitoring_service.monitor_margins)
        return {"status": "success", "message": "Monitoring service started"}
    else:
        return {"status": "info", "message": "Monitoring service already running"}


@router.post("/stop-monitoring")
async def stop_monitoring():
    """Stop the LP margin monitoring service."""
    monitoring_service.stop_monitoring()
    return {"status": "success", "message": "Monitoring service stopped"}


@router.get("/monitoring-status")
async def get_monitoring_status():
    """Get current monitoring service status."""
    return {
        "status": "running" if monitoring_service.is_running else "stopped",
        "threshold": MARGIN_THRESHOLD,
        "interval": MONITORING_INTERVAL,
        "last_alerts": {
            lp: alert_time.isoformat() 
            for lp, alert_time in monitoring_service.last_alerts.items()
        }
    }


@router.post("/test-alert")
async def test_alert(lp_name: str = "TEST_LP", margin_level: float = 85.0):
    """Test alert functionality by logging a test alert."""
    logger.warning(f"🚨 TEST ALERT: {lp_name} margin level {margin_level:.2f}% exceeds threshold {MARGIN_THRESHOLD}%")
    return {
        "status": "success",
        "message": f"Test alert logged for {lp_name}"
    }
