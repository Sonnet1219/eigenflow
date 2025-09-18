"""
EigenFlow API Data Gateway for external API interactions.

Contains the EigenFlowAPI class for:
- Authentication with EigenFlow API
- LP account information retrieval
- LP position data fetching
"""

import os
import logging
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime

import dotenv
dotenv.load_dotenv()

logger = logging.getLogger(__name__)

# API Configuration
API_BASE_URL = "https://api-anshin.sigmarisk.com.au/api/v1"
AUTH_ENDPOINT = f"{API_BASE_URL}/auth"
LP_ACCOUNT_ENDPOINT = f"{API_BASE_URL}/lp/account"
LP_POSITION_ENDPOINT = f"{API_BASE_URL}/lp/position"

# Configuration Settings
CONFIG = {
    # API timeouts
    'API_TIMEOUT_SECONDS': 30,
}

# LP ID to Name Mapping
LP_MAPPING = {
    143: "[CFH] MAJESTIC FIN TRADE",
    142: "[GBEGlobal]GBEGlobal1"
}

# Reverse mapping for name to ID lookup
LP_NAME_TO_ID = {name: lp_id for lp_id, name in LP_MAPPING.items()}


def get_lp_mapping_string() -> str:
    """Generate LP mapping string for prompt injection."""
    return ", ".join([f'"{name}"->{lp_id}' for lp_id, name in LP_MAPPING.items()])

class EigenFlowAPI:
    """EigenFlow API client for LP data retrieval."""
    
    def __init__(self):
        self.access_token = None
        self.headers = {"Content-Type": "application/json"}
    
    def authenticate(self, email: str = None, password: str = None, broker: str = None) -> Dict[str, Any]:
        """Authenticate with EigenFlow API and get access token."""
        # Use environment variables if credentials not provided
        email = email or os.getenv("EIGENFLOW_EMAIL")
        password = password or os.getenv("EIGENFLOW_PASSWORD") 
        broker = broker or os.getenv("EIGENFLOW_BROKER")
        
        if not email or not password or not broker:
            return {
                "success": False, 
                "error": "Email, password and broker required. Set EIGENFLOW_EMAIL, EIGENFLOW_PASSWORD and EIGENFLOW_BROKER env vars."
            }
        
        try:
            auth_data = {
                "email": email,
                "password": password,
                "broker": broker  # Use the pre-hashed broker value directly
            }
            
            response = requests.post(AUTH_ENDPOINT, json=auth_data, headers=self.headers, timeout=CONFIG['API_TIMEOUT_SECONDS'])
            
            if response.status_code == 200:
                auth_result = response.json()
                self.access_token = auth_result.get("access_token")
                
                if self.access_token:
                    # Update headers with authorization
                    self.headers["Authorization"] = f"Bearer {self.access_token}"
                    logger.info("Successfully authenticated with EigenFlow API")
                    return {"success": True, "message": "Authentication successful"}
                else:
                    return {"success": False, "error": "No access token received"}
            else:
                return {
                    "success": False, 
                    "error": f"Authentication failed: {response.status_code} - {response.text}"
                }
                
        except requests.RequestException as e:
            logger.error(f"Authentication request failed: {e}")
            return {"success": False, "error": f"Request failed: {str(e)}"}
    
    def get_lp_account(self, lp_id: Optional[int] = None, lp_name: Optional[str] = None) -> Dict[str, Any]:
        """Get LP account information by ID or name."""
        if not self.access_token:
            return {"success": False, "error": "Not authenticated. Please login first."}
        
        try:
            params = {}
            if lp_id is not None:
                params["lp_id"] = lp_id
            if lp_name is not None:
                params["lp_name"] = lp_name
                
            response = requests.get(LP_ACCOUNT_ENDPOINT, params=params, headers=self.headers, timeout=CONFIG['API_TIMEOUT_SECONDS'])
            
            if response.status_code == 200:
                account_data = response.json()
                logger.info(f"Retrieved LP account data: {len(account_data) if isinstance(account_data, list) else 1} accounts")
                return {"success": True, "data": account_data}
            else:
                return {
                    "success": False,
                    "error": f"Failed to get account data: {response.status_code} - {response.text}"
                }
                
        except requests.RequestException as e:
            logger.error(f"LP account request failed: {e}")
            return {"success": False, "error": f"Request failed: {str(e)}"}
    
    def get_lp_positions(self, lp_id: Optional[int] = None, lp_name: Optional[str] = None) -> Dict[str, Any]:
        """Get LP position information by ID or name."""
        if not self.access_token:
            return {"success": False, "error": "Not authenticated. Please login first."}
        
        try:
            params = {}
            if lp_id is not None:
                params["lp_id"] = lp_id
            if lp_name is not None:
                params["lp_name"] = lp_name
                
            response = requests.get(LP_POSITION_ENDPOINT, params=params, headers=self.headers, timeout=CONFIG['API_TIMEOUT_SECONDS'])
            
            if response.status_code == 200:
                position_data = response.json()
                logger.info(f"Retrieved LP position data: {len(position_data) if isinstance(position_data, list) else 1} positions")
                return {"success": True, "data": position_data}
            else:
                return {
                    "success": False,
                    "error": f"Failed to get position data: {response.status_code} - {response.text}"
                }
                
        except requests.RequestException as e:
            logger.error(f"LP position request failed: {e}")
            return {"success": False, "error": f"Request failed: {str(e)}"}
    
    def get_lp_accounts(self) -> List[Dict[str, Any]]:
        """Get all LP accounts for monitoring."""
        if not self.access_token:
            logger.error("Not authenticated. Please login first.")
            return []
        
        try:
            response = requests.get(LP_ACCOUNT_ENDPOINT, headers=self.headers, timeout=CONFIG['API_TIMEOUT_SECONDS'])
            
            if response.status_code == 200:
                account_data = response.json()
                logger.info(f"Retrieved LP account data: {len(account_data) if isinstance(account_data, list) else 1} accounts")
                return account_data if isinstance(account_data, list) else [account_data]
            else:
                logger.error(f"Failed to get account data: {response.status_code} - {response.text}")
                return []
                
        except requests.RequestException as e:
            logger.error(f"LP account request failed: {e}")
            return []
