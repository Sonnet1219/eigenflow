from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid
from dataclasses import dataclass


# ============================================================================
# 1. Intent Recognition Schemas
# ============================================================================


class IntentScope(BaseModel):
    """Scope information for intent classification."""
    currentLevel: Optional[str] = Field(default="lp", description="Current operation level")
    brokerId: Optional[str] = Field(default=None, description="Broker identifier")
    lp: Optional[str] = Field(default=None, description="LP name for filtering (exact match from mapping table)") 
    group: Optional[str] = Field(default=None, description="Group identifier")
    # fallback:bool = Field(default=False, description="Fallback or not")


class IntentContext(BaseModel):
    """Enhanced intent context with detailed classification information."""
    schemaVer: str = Field(default="dc/v1", description="Schema version identifier")
    intent: str = Field(default="margin_check", description="Classified intent")
    confidence: float = Field(default=0.0, description="Classification confidence score (0-1)")
    slots: Dict[str, Any] = Field(default_factory=dict, description="Contextual slot information")
    traceId: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique trace identifier")
    occurredAt: str = Field(default_factory=lambda: datetime.now().isoformat() + "Z", description="ISO8601 timestamp of occurrence")


class IntentClassification(BaseModel):
    """Enhanced intent classification result with detailed context."""
    schemaVer: str = Field(default="dc/v1", description="Schema version identifier")
    intent: Literal['general_conversation', 'lp_margin_check_report'] = Field(
        description="The classified user intent"
    )
    confidence: float = Field(description="Classification confidence score (0-1)")
    slots: IntentScope = Field(default_factory=IntentScope, description="Contextual scope information")
    traceId: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique trace identifier")
    occurredAt: str = Field(default_factory=lambda: datetime.now().isoformat() + "Z", description="ISO8601 timestamp of occurrence")


# ============================================================================
# 2. Orchestrator Input Schemas
# ============================================================================

class OrchestratorScope(BaseModel):
    """Scope information for orchestrator input."""
    currentLevel: str = Field(default="lp", description="Current operation level")
    brokerId: Optional[str] = Field(default=None, description="Broker identifier")
    lp: Optional[str] = Field(default=None, description="LP identifier")
    group: Optional[str] = Field(default=None, description="Group identifier")


class OrchestratorOptions(BaseModel):
    """Options for orchestrator processing."""
    requirePositionsDepth: bool = Field(default=True, description="Include detailed position data")
    detectCrossPositions: bool = Field(default=True, description="Detect cross-position opportunities")
    requireExplain: bool = Field(default=True, description="Include explanations in results")


@dataclass
class OrchestratorScope:
    """Scope information for orchestrator operations."""
    currentLevel: str = "lp"
    brokerId: Optional[str] = None
    lp: Optional[str] = None
    group: Optional[str] = None


@dataclass
class OrchestratorInputs:
    """Input parameters for orchestrator operations."""
    scope: OrchestratorScope = None
    lps: List[str] = None
    timepoint: Optional[str] = None
    options: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.scope is None:
            self.scope = OrchestratorScope()
        if self.lps is None:
            self.lps = []
        if self.options is None:
            self.options = {}


class OrchestratorInput(BaseModel):
    """Input format for orchestrator (supervisor)."""
    schemaVer: str = Field(default="dc/v1", description="Schema version")
    tool: str = Field(description="Tool to execute")
    inputs: Dict[str, Any] = Field(description="Tool input parameters")
    tenantId: str = Field(description="Tenant identifier")
    traceId: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique trace identifier")
    idempotencyKey: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Idempotency key")
    occurredAt: str = Field(default_factory=lambda: datetime.now().isoformat() + "Z", description="ISO8601 timestamp")


# ============================================================================
# 3. External Data Interface Schemas (DataGateway Return Format)
# ============================================================================

class Account(BaseModel):
    """Account information."""
    acctId: str = Field(description="Account identifier")
    lp: str = Field(description="LP identifier")
    group: str = Field(description="Group identifier")


class Balance(BaseModel):
    """Account balance information."""
    acctId: str = Field(description="Account identifier")
    lp: str = Field(description="LP identifier")
    equity: float = Field(description="Account equity")
    balance: float = Field(description="Account balance")
    marginUsed: float = Field(description="Margin currently used")
    freeMargin: float = Field(description="Free margin available")
    leverage: float = Field(description="Account leverage")


class Position(BaseModel):
    """Position information."""
    id: str = Field(description="Position identifier")
    lp: str = Field(description="LP identifier")
    symbol: str = Field(description="Trading symbol")
    side: Literal["buy", "sell"] = Field(description="Position side")
    volume: float = Field(description="Position volume")
    price: float = Field(description="Position price")
    margin: float = Field(description="Position margin")
    swap: float = Field(description="Position swap")
    timestamp: str = Field(description="ISO8601 timestamp")


class RiskIndicator(BaseModel):
    """Risk indicator information."""
    lp: str = Field(description="LP identifier")
    marginLevel: float = Field(description="Margin level percentage")
    exposure: float = Field(description="Total exposure")


class Threshold(BaseModel):
    """Risk threshold configuration."""
    scope: str = Field(description="Threshold scope")
    lp: str = Field(description="LP identifier")
    warn: float = Field(description="Warning threshold")
    critical: float = Field(description="Critical threshold")
    source: str = Field(description="Threshold source")


class Spread(BaseModel):
    """Spread information."""
    instrumentId: str = Field(description="Instrument identifier")
    lp: str = Field(description="LP identifier")
    value: float = Field(description="Spread value")
    ts: str = Field(description="ISO8601 timestamp")


class Swap(BaseModel):
    """Swap information."""
    instrumentId: str = Field(description="Instrument identifier")
    lp: str = Field(description="LP identifier")
    long: float = Field(description="Long swap rate (USD per day)")
    short: float = Field(description="Short swap rate (USD per day)")


class Commission(BaseModel):
    """Commission information."""
    lp: str = Field(description="LP identifier")
    perLot: float = Field(description="Commission per lot (USD)")


class CostsAndMarket(BaseModel):
    """Market costs and pricing information."""
    spreads: List[Spread] = Field(description="Spread information")
    swaps: List[Swap] = Field(description="Swap information")
    commissions: List[Commission] = Field(description="Commission information")


class AllowedSymbol(BaseModel):
    """Allowed symbols for LP."""
    lp: str = Field(description="LP identifier")
    symbols: List[str] = Field(description="Allowed trading symbols")


class TradingWindow(BaseModel):
    """Trading window configuration."""
    lp: str = Field(description="LP identifier")
    from_time: str = Field(alias="from", description="Start time (HH:MMZ)")
    to_time: str = Field(alias="to", description="End time (HH:MMZ)")


class Limit(BaseModel):
    """Trading limit configuration."""
    lp: str = Field(description="LP identifier")
    type: str = Field(description="Limit type")
    value: float = Field(description="Limit value")


class RoutingConstraints(BaseModel):
    """Routing constraints configuration."""
    allowedSymbols: List[AllowedSymbol] = Field(description="Allowed symbols per LP")
    tradingWindows: List[TradingWindow] = Field(description="Trading windows per LP")
    limits: List[Limit] = Field(description="Trading limits per LP")


class DataSource(BaseModel):
    """Data source lineage information."""
    name: str = Field(description="Source name")
    ts: str = Field(description="ISO8601 timestamp")


class Lineage(BaseModel):
    """Data lineage information."""
    sources: List[DataSource] = Field(description="Data sources")


class Freshness(BaseModel):
    """Data freshness information."""
    maxAgeSec: int = Field(description="Maximum age in seconds")
    perSource: Dict[str, int] = Field(description="Age per source in seconds")
    degraded: bool = Field(description="Whether data is degraded")


class Normalization(BaseModel):
    """Data normalization settings."""
    money: str = Field(default="USD", description="Base currency")
    volume: str = Field(default="lot", description="Volume unit")
    marginLevelFormat: str = Field(default="percent", description="Margin level format")
    spreadDefinition: str = Field(default="price_diff", description="Spread definition")
    swapPeriod: str = Field(default="day", description="Swap calculation period")
    rounding: Dict[str, int] = Field(default={"money": 2, "priceDefaultScale": 5}, description="Rounding settings")


class ExternalDataResponse(BaseModel):
    """External data interface response format."""
    schemaVer: str = Field(default="dc/v1-lean", description="Schema version")
    snapshotId: str = Field(description="Snapshot identifier")
    snapshotTs: str = Field(description="Snapshot timestamp (ISO8601)")
    normalization: Normalization = Field(description="Data normalization settings")
    accounts: List[Account] = Field(description="Account information")
    balances: List[Balance] = Field(description="Balance information")
    positions: List[Position] = Field(description="Position information")
    riskIndicators: List[RiskIndicator] = Field(description="Risk indicators")
    thresholds: List[Threshold] = Field(description="Risk thresholds")
    costsAndMarket: CostsAndMarket = Field(description="Market costs and pricing")
    routingConstraints: RoutingConstraints = Field(description="Routing constraints")
    lineage: Lineage = Field(description="Data lineage")
    freshness: Freshness = Field(description="Data freshness")


# ============================================================================
# 4. Margin Check Tool Response Schemas
# ============================================================================


class SupervisorRouting(BaseModel):
    """Supervisor's routing decision."""
    next_agent: Literal['ai_responder', 'FINISH'] = Field(
        description="The next agent to route to or FINISH if task is complete"
    )


# Margin Check Tool Structured Response Schemas

class MarginMetrics(BaseModel):
    """Overall margin portfolio metrics."""
    avgMarginLevel: float = Field(description="Average margin level across all LPs")
    lpCount: int = Field(description="Number of LP accounts")


class ThresholdsRef(BaseModel):
    """Risk threshold references."""
    warn: float = Field(description="Warning threshold")
    critical: float = Field(description="Critical threshold")


class PerLPMetrics(BaseModel):
    """Per-LP margin metrics with integrated position summary."""
    lp: str = Field(description="LP identifier")
    balance: float = Field(description="Account balance")
    credit: float = Field(description="Credit amount")
    equity: float = Field(description="Account equity")
    marginUsed: float = Field(description="Margin currently used")
    freeMargin: float = Field(description="Free margin available")
    marginLevel: float = Field(description="Margin utilization percentage")
    unrealizedPnL: float = Field(description="Unrealized profit and loss")
    dataTimestamp: str = Field(description="Data timestamp from API")
    # Position summary data
    totalPositions: int = Field(description="Total number of positions")
    totalVolume: float = Field(description="Total position volume (absolute)")
    totalExposure: float = Field(description="Total market exposure")
    avgMarginRate: float = Field(description="Average margin rate across positions")
    topSymbols: List[str] = Field(description="Top 3 symbols by volume")
    # Risk and alert data
    thresholdsRef: ThresholdsRef = Field(description="Risk thresholds for this LP")
    alertStatus: Optional[int] = Field(default=None, description="Alert status: 1=alert, 0=safe")
    alertMessage: Optional[str] = Field(default=None, description="Alert message if applicable")


class VolumePair(BaseModel):
    """Volume pair for cross-position analysis."""
    a: float = Field(description="Volume for LP A")
    b: float = Field(description="Volume for LP B")



class CrossCandidate(BaseModel):
    """Cross-position netting candidate."""
    symbol: str = Field(description="Trading symbol")
    lpA: str = Field(description="First LP identifier")
    lpB: str = Field(description="Second LP identifier")
    volumePair: VolumePair = Field(description="Volume pair for netting")
    releasableMargin: float = Field(description="Potential margin release amount")


class MoveCandidate(BaseModel):
    """Position move candidate."""
    fromLP: str = Field(description="Source LP")
    toLP: str = Field(description="Target LP")
    symbol: str = Field(description="Trading symbol")
    volume: float = Field(description="Volume to move")
    rationale: str = Field(description="Reason for the move")


class ImpactMetrics(BaseModel):
    """Impact metrics for recommendations."""
    mlBefore: float = Field(description="Margin level before action")
    mlAfter: float = Field(description="Margin level after action")


class Driver(BaseModel):
    """Key driver factor for recommendation."""
    k: str = Field(description="Driver key (e.g., marginUsed, exposure)")
    delta: float = Field(description="Expected change amount")


class ExplainMetrics(BaseModel):
    """Explanation metrics for recommendations."""
    whyNow: str = Field(description="Why this recommendation is needed now")
    drivers: List[Driver] = Field(description="Key driving factors")
    confidence: float = Field(description="Confidence score (0-1)")


class Action(BaseModel):
    """Actionable step for recommendation."""
    tool: str = Field(description="Tool or method to execute")
    params: Dict[str, Any] = Field(description="Parameters for the action")
    idempotencyKey: str = Field(description="Unique key for idempotent execution")


class Recommendation(BaseModel):
    """Risk management recommendation."""
    id: str = Field(description="Unique recommendation identifier")
    type: Literal["CLEAR_CROSS", "MOVE", "DEPOSIT", "WAREHOUSE"] = Field(description="Recommendation type")
    priority: int = Field(description="Priority level (1=highest)")
    impact: ImpactMetrics = Field(description="Expected impact metrics")
    explain: ExplainMetrics = Field(description="Explanation and rationale")
    actions: List[Action] = Field(description="Actionable steps")


class MarginCheckToolResponse(BaseModel):
    """Structured response from margin check tool."""
    schemaVer: str = Field(default="dc/v1", description="Schema version")
    status: Literal["ok", "warn", "critical"] = Field(description="Overall risk status")
    metrics: MarginMetrics = Field(description="Portfolio-wide metrics")
    normalization: Normalization = Field(description="Data normalization settings")
    perLP: List[PerLPMetrics] = Field(description="Per-LP detailed metrics with position summaries")
    crossCandidates: List[CrossCandidate] = Field(description="Cross-position netting opportunities")
    moveCandidates: List[MoveCandidate] = Field(description="Position move recommendations")
    recommendations: List[Recommendation] = Field(description="Actionable risk management recommendations")
    traceId: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique trace identifier")