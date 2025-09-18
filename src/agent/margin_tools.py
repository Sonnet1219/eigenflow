"""
LP Margin Analysis Tools for risk assessment and recommendation generation.

Contains tools for:
- LP margin checking and analysis
- Risk threshold evaluation
- Cross-position netting analysis
- Position move recommendations
"""

import json
import logging
from typing import Dict, Any, List
from datetime import datetime
from langchain_core.tools import tool
import uuid

from .data_gateway import EigenFlowAPI, LP_MAPPING, LP_NAME_TO_ID

logger = logging.getLogger(__name__)

# Configuration Settings
CONFIG = {
    # Risk thresholds (standardized)
    'MARGIN_ALERT_THRESHOLD': 80.0,  # Standard margin utilization alert threshold
    
    # Analysis parameters
    'LOW_RISK_MULTIPLIER': 0.7,  # For determining low-risk LPs
    'SIGNIFICANT_MARGIN_THRESHOLD': 10000.0,  # Minimum margin to consider for moves
    'CROSS_MARGIN_THRESHOLD': 5000.0,  # Minimum margin release for cross recommendations
    'MOVE_VOLUME_RATIO': 0.5,  # Maximum percentage of position to move
    'MAX_MOVE_VOLUME': 100.0,  # Maximum volume to move in lots
    
    # Data freshness
    'DATA_DEGRADED_THRESHOLD_SEC': 300,  # 5 minutes
    
    # Default values
    'DEFAULT_LEVERAGE': 100.0,
    'DEFAULT_PRICE': 0.0,
    'DEFAULT_SWAP': 0.0,
    
    # Confidence scores
    'CROSS_RECOMMENDATION_CONFIDENCE': 0.85,
    'MOVE_RECOMMENDATION_CONFIDENCE': 0.75,
    
    # Estimated impacts
    'ESTIMATED_MARGIN_REDUCTION': 5000.0,
    
    # Schema and formatting
    'SCHEMA_VERSION': 'dc/v1-lean',
    'MONEY_PRECISION': 2,
    'PRICE_PRECISION': 5
}

# Global API client instance
api_client = EigenFlowAPI()


@tool
def get_lp_margin_check(lp_name: str = None) -> str:
    """
    Get comprehensive LP margin and risk data from EigenFlow API.
    
    This tool performs a complete pipeline:
    1. Authenticates with the API
    2. Retrieves LP account information (all LPs or specific LP)
    3. Fetches current positions
    4. Returns structured JSON data for analysis
    
    Args:
        lp_name: Optional LP name to filter results. If provided, only data for this LP will be returned.
                 Supported values: "[CFH] MAJESTIC FIN TRADE", "[GBEGlobal]GBEGlobal1"
    
    Returns structured JSON containing accounts, balances, positions, risk indicators, and metadata.
    """
    try:
        # Step 1: Authenticate
        auth_result = api_client.authenticate()
        if not auth_result["success"]:
            return f"❌ Authentication failed: {auth_result['error']}"
        
        # Step 2: Determine LP ID if specific LP requested
        lp_id = None
        if lp_name:
            lp_id = LP_NAME_TO_ID.get(lp_name)
            if lp_id is None:
                available_lps = list(LP_NAME_TO_ID.keys())
                return f"❌ Unknown LP name: {lp_name}. Available LPs: {available_lps}"
        
        # Step 3: Get LP account data
        account_result = api_client.get_lp_account(lp_id)
        if not account_result["success"]:
            return f"❌ Failed to retrieve account data: {account_result['error']}"
        
        # Step 4: Get LP positions (filter by same LP ID if specified)
        position_result = api_client.get_lp_positions(lp_id)
        if not position_result["success"]:
            return f"❌ Failed to retrieve position data: {position_result['error']}"
        
        # Step 4: Generate structured JSON data
        account_data = account_result["data"]
        position_data = position_result["data"]
        
        # Generate analysis and return MarginCheckToolResponse format
        margin_response = generate_margin_analysis(account_data, position_data)
        return json.dumps(margin_response, indent=2)
        
    except Exception as e:
        logger.error(f"LP margin report generation failed: {e}")
        return f"❌ Report generation failed: {str(e)}"


def lp_margin_check_report(lp_account_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Check LP margin utilization and generate alerts for accounts >= 80% usage.
    
    Args:
        lp_account_info: List of LP account info dictionaries containing
                        'Margin Utilization %' and 'LP' fields.
    
    Returns:
        List of dictionaries with alert status, LP name, message, and account data.
    """
    result = []

    for info in lp_account_info:
        margin_util = info.get('Margin Utilization %', 0)
        lp_name = info.get('LP', 'Unknown')
        
        if margin_util >= CONFIG['MARGIN_ALERT_THRESHOLD']:
            result.append({
                "is_alert": 1,
                "lp": lp_name,
                "message": "Margin level 80% reached, please watch out.",
                "account_data": info
            })
        else:
            result.append({
                "is_alert": 0,
                "lp": lp_name,
                "message": "Current margin under safe line. No further action needed",
                "account_data": info
            })

    return result


def generate_margin_analysis(account_data: Any, position_data: Any) -> Dict[str, Any]:
    """Generate margin analysis in MarginCheckToolResponse format."""
    
    # Ensure data is in list format for processing
    accounts = account_data if isinstance(account_data, list) else [account_data]
    positions = position_data if isinstance(position_data, list) else [position_data]
    
    current_time = datetime.now()
    trace_id = str(uuid.uuid4())
    
    # Initialize MarginCheckToolResponse structure
    per_lp_metrics = []
    cross_candidates = []
    move_candidates = []
    recommendations = []
    
    # Calculate portfolio-wide metrics
    total_equity = 0
    total_margin = 0
    lp_margin_levels = []
    
    # Process account data for per-LP metrics
    for account in accounts:
        lp_name = account.get("LP", "Unknown")
        balance = account.get("Balance", 0)
        credit = account.get("Credit", 0)
        equity = account.get("Equity", 0)
        margin = account.get("Margin", 0)
        free_margin = account.get("Free Margin", 0)
        margin_util = account.get("Margin Utilization %", 0)
        unrealized_pnl = account.get("Unrealized P&L", 0)
        data_timestamp = account.get("updated_at", "")
        
        total_equity += equity
        total_margin += margin
        lp_margin_levels.append(margin_util)
        
        # Generate alert status using standard function
        alert_result = lp_margin_check_report([account])[0]
        
        # Build per-LP metrics (will be updated with position summary later)
        per_lp_metrics.append({
            "lp": lp_name,
            "balance": float(balance),
            "credit": float(credit),
            "equity": float(equity),
            "marginUsed": float(margin),
            "freeMargin": float(free_margin),
            "marginLevel": float(margin_util),
            "unrealizedPnL": float(unrealized_pnl),
            "dataTimestamp": data_timestamp,
            "totalPositions": 0,
            "totalVolume": 0.0,
            "totalExposure": 0.0,
            "avgMarginRate": 0.0,
            "topSymbols": [],
            "thresholdsRef": {
                "warn": CONFIG['MARGIN_ALERT_THRESHOLD'],
                "critical": CONFIG['MARGIN_ALERT_THRESHOLD']
            },
            "alertStatus": alert_result["is_alert"],
            "alertMessage": alert_result["message"]
        })
    
    # Calculate average margin level
    avg_margin_level = sum(lp_margin_levels) / len(lp_margin_levels) if lp_margin_levels else 0
    
    # Determine overall status - only warn/critical for high margin usage
    status = "ok"
    if any(ml >= CONFIG['MARGIN_ALERT_THRESHOLD'] for ml in lp_margin_levels):
        status = "critical"
    
    # Process positions and calculate LP-level summaries
    position_by_symbol = {}
    lp_position_summaries = {}
    
    for position in positions:
        symbol = position.get("Symbol", "N/A")
        if symbol == "N/A":
            continue
            
        lp_name = position.get("LP", "Unknown")
        position_size = position.get("Position", 0)
        margin_used = position.get("Margin", 0)
        margin_rate = position.get("Margin Rate", 0)
        contract_size = position.get("Contract Size", 100000)  # Default forex contract size
        
        # Skip positions with zero size
        if position_size == 0:
            continue
        
        side = "buy" if float(position_size) > 0 else "sell"
        volume = abs(float(position_size))
        
        # Calculate market exposure (volume * contract_size)
        exposure = volume * (float(contract_size) if contract_size else 100000)
        
        # Update LP position summary
        if lp_name not in lp_position_summaries:
            lp_position_summaries[lp_name] = {
                "positions": 0,
                "total_volume": 0.0,
                "total_exposure": 0.0,
                "margin_rates": [],
                "symbols": {}
            }
        
        summary = lp_position_summaries[lp_name]
        summary["positions"] += 1
        summary["total_volume"] += volume
        summary["total_exposure"] += exposure
        if margin_rate > 0:
            summary["margin_rates"].append(float(margin_rate))
        
        # Track symbol volumes for top symbols
        if symbol not in summary["symbols"]:
            summary["symbols"][symbol] = 0
        summary["symbols"][symbol] += volume
        
        # For cross-position analysis
        if symbol not in position_by_symbol:
            position_by_symbol[symbol] = []
            
        position_by_symbol[symbol].append({
            "lp": lp_name,
            "side": side,
            "volume": volume,
            "margin": float(margin_used),
            "position_size": float(position_size)
        })
    
    # Update per-LP metrics with position summaries
    for lp_metric in per_lp_metrics:
        lp_name = lp_metric["lp"]
        if lp_name in lp_position_summaries:
            summary = lp_position_summaries[lp_name]
            lp_metric["totalPositions"] = summary["positions"]
            lp_metric["totalVolume"] = round(summary["total_volume"], 2)
            lp_metric["totalExposure"] = round(summary["total_exposure"], 2)
            lp_metric["avgMarginRate"] = round(sum(summary["margin_rates"]) / len(summary["margin_rates"]), 4) if summary["margin_rates"] else 0.0
            
            # Get top 3 symbols by volume
            top_symbols = sorted(summary["symbols"].items(), key=lambda x: x[1], reverse=True)[:3]
            lp_metric["topSymbols"] = [symbol for symbol, _ in top_symbols]
    
    # Calculate LP-level margin per unit for estimation
    lp_margin_rates = {}
    for account in accounts:
        lp_name = account.get("LP", "Unknown")
        total_margin = account.get("Margin", 0)
        
        # Calculate total position volume for this LP
        lp_positions = [p for p in positions if p.get("LP") == lp_name and p.get("Position", 0) != 0]
        total_volume = sum(abs(p.get("Position", 0)) for p in lp_positions)
        
        if total_volume > 0:
            lp_margin_rates[lp_name] = total_margin / total_volume
        else:
            lp_margin_rates[lp_name] = 0
    
    # Generate cross-position netting candidates
    for symbol, pos_list in position_by_symbol.items():
        for i, pos1 in enumerate(pos_list):
            for pos2 in pos_list[i+1:]:
                # Check if different LPs and opposite sides (buy vs sell)
                if pos1["lp"] != pos2["lp"] and pos1["side"] != pos2["side"]:
                    nettable_vol = min(pos1["volume"], pos2["volume"])
                    if nettable_vol > 0:
                        # Estimate margin for each position using LP margin rate
                        lp1_margin_rate = lp_margin_rates.get(pos1["lp"], 0)
                        lp2_margin_rate = lp_margin_rates.get(pos2["lp"], 0)
                        
                        pos1_estimated_margin = pos1["volume"] * lp1_margin_rate
                        pos2_estimated_margin = pos2["volume"] * lp2_margin_rate
                        
                        # Calculate releasable margin (conservative estimate)
                        if pos1_estimated_margin > 0 and pos2_estimated_margin > 0:
                            # Margin released = smaller position's margin * netting ratio
                            releasable_margin = min(pos1_estimated_margin, pos2_estimated_margin) * (nettable_vol / max(pos1["volume"], pos2["volume"]))
                        else:
                            releasable_margin = 0
                        
                        cross_candidates.append({
                            "symbol": symbol,
                            "lpA": pos1["lp"],
                            "lpB": pos2["lp"],
                            "volumePair": {"a": pos1["volume"], "b": pos2["volume"]},
                            "releasableMargin": round(releasable_margin, 2)
                        })
    
    # Generate position reduction candidates for high-risk LPs only
    high_risk_lps = [lp["lp"] for lp in per_lp_metrics if lp["marginLevel"] >= CONFIG['MARGIN_ALERT_THRESHOLD']]
    
    for high_lp in high_risk_lps:
        for symbol, pos_list in position_by_symbol.items():
            high_positions = [p for p in pos_list if p["lp"] == high_lp]
            if high_positions:
                pos = high_positions[0]
                # Suggest reducing position size to lower margin usage
                reduce_volume = min(pos["volume"] * CONFIG['MOVE_VOLUME_RATIO'], CONFIG['MAX_MOVE_VOLUME'])
                if reduce_volume > 0:
                    move_candidates.append({
                        "fromLP": high_lp,
                        "toLP": "MOVE",  # Indicate position move or reduction
                        "symbol": symbol,
                        "volume": reduce_volume,
                        "rationale": "Reduce position size to lower margin utilization"
                    })
    
    # Generate recommendations - Cross Position clearing is ALWAYS highest priority (P0)
    rec_id = 1
    # Sort cross candidates by releasable margin (highest first)
    sorted_cross_candidates = sorted(cross_candidates, key=lambda x: x['releasableMargin'], reverse=True)
    
    # Check if any LP has margin utilization >= threshold to determine alert urgency
    has_high_margin_lp = any(lp["marginLevel"] >= CONFIG['MARGIN_ALERT_THRESHOLD'] for lp in per_lp_metrics)
    
    # Generate recommendations only for high margin situations
    if has_high_margin_lp:
        # Cross Position recommendations for margin reduction
        for cross in sorted_cross_candidates[:3]:  # Limit to top 3
            if cross['releasableMargin'] > 0:
                # Calculate actual impact on margin level
                total_portfolio_margin = sum(account.get("Margin", 0) for account in accounts)
                if total_portfolio_margin > 0:
                    ml_improvement = (cross['releasableMargin'] / total_portfolio_margin) * 100
                    ml_after = max(0, avg_margin_level - ml_improvement)
                else:
                    ml_after = avg_margin_level
                
                recommendations.append({
                    "id": f"REC-{rec_id:03d}",
                    "type": "CLEAR_CROSS",
                    "priority": 0,
                    "impact": {
                        "mlBefore": round(avg_margin_level, 2),
                        "mlAfter": round(ml_after, 2)
                    },
                    "explain": {
                        "whyNow": f"URGENT: Cross-netting {cross['symbol']} positions can release ${cross['releasableMargin']:,.0f} margin to reduce risk",
                        "drivers": [{"k": "marginUsed", "delta": -cross['releasableMargin']}],
                        "confidence": CONFIG['CROSS_RECOMMENDATION_CONFIDENCE']
                    },
                    "actions": [{
                        "tool": "execute_cross_netting",
                        "params": {"symbol": cross['symbol'], "lpA": cross['lpA'], "lpB": cross['lpB']},
                        "idempotencyKey": f"NET-{cross['symbol']}-{current_time.strftime('%Y%m%d')}-{rec_id:03d}"
                    }]
                })
                rec_id += 1
        
        # Position reduction recommendations
        for move in move_candidates[:2]:  # Limit to top 2
            recommendations.append({
                "id": f"REC-{rec_id:03d}",
                "type": "MOVE",
                "priority": 1,
                "impact": {
                    "mlBefore": round(avg_margin_level, 2),
                    "mlAfter": round(avg_margin_level - 5, 2)  # Estimated reduction
                },
                "explain": {
                    "whyNow": f"Reduce {move['symbol']} position size by {move['volume']} lots to lower margin usage",
                    "drivers": [{"k": "positionSize", "delta": -move['volume']}],
                    "confidence": CONFIG['MOVE_RECOMMENDATION_CONFIDENCE']
                },
                "actions": [{
                    "tool": "reduce_position",
                    "params": {"symbol": move['symbol'], "lp": move['fromLP'], "volume": move['volume']},
                    "idempotencyKey": f"RED-{move['symbol']}-{current_time.strftime('%Y%m%d')}-{rec_id:03d}"
                }]
            })
            rec_id += 1
    
    # Calculate data quality metrics
    timestamps = []
    missing_fields = []
    
    # Collect timestamps from accounts and positions
    for account in accounts:
        if account.get("updated_at"):
            timestamps.append(account["updated_at"])
    
    for position in positions:
        if position.get("updated_at"):
            timestamps.append(position["updated_at"])
    
    # Calculate data age and freshness
    if timestamps:
        oldest_ts = min(timestamps)
        newest_ts = max(timestamps)
        
        # Parse timestamps and calculate age
        try:
            oldest_dt = datetime.strptime(oldest_ts, "%Y-%m-%d %H:%M:%S")
            data_age = int((current_time - oldest_dt).total_seconds())
            is_fresh = data_age <= CONFIG['DATA_DEGRADED_THRESHOLD_SEC']
        except:
            data_age = 0
            is_fresh = True
            oldest_ts = current_time.strftime("%Y-%m-%d %H:%M:%S")
            newest_ts = current_time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        data_age = 0
        is_fresh = True
        oldest_ts = current_time.strftime("%Y-%m-%d %H:%M:%S")
        newest_ts = current_time.strftime("%Y-%m-%d %H:%M:%S")
    
    # Check for missing important fields
    for account in accounts:
        if not account.get("Credit"):
            missing_fields.append("Credit")
        if not account.get("Unrealized P&L"):
            missing_fields.append("Unrealized P&L")
    
    for position in positions:
        if not position.get("Margin Rate"):
            missing_fields.append("Margin Rate")
        if not position.get("Contract Size"):
            missing_fields.append("Contract Size")
    
    missing_fields = list(set(missing_fields))  # Remove duplicates
    quality_score = max(0.0, 1.0 - (len(missing_fields) * 0.1) - (0.2 if not is_fresh else 0.0))
    
    # Build final MarginCheckToolResponse
    margin_response = {
        "schemaVer": "dc/v1",
        "status": status,
        "metrics": {
            "avgMarginLevel": round(avg_margin_level, 2),
            "lpCount": len(per_lp_metrics)
        },
        "normalization": {
            "money": "USD",
            "volume": "lot",
            "marginLevelFormat": "percent",
            "spreadDefinition": "price_diff",
            "swapPeriod": "day",
            "rounding": {"money": CONFIG['MONEY_PRECISION'], "priceDefaultScale": CONFIG['PRICE_PRECISION']}
        },
        "perLP": per_lp_metrics,
        "crossCandidates": cross_candidates,
        "moveCandidates": move_candidates,
        "recommendations": recommendations,
        "traceId": trace_id
    }
    
    return margin_response
