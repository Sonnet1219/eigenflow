"""Response models for the API."""

from typing import Optional
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Error response model."""

    code: int = Field(400, description="Error code")
    message: str = Field("Error", description="Error message")
    detail: Optional[str] = Field(None, description="Error details")
