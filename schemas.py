"""
Database Schemas for Toolkit Converter

Each Pydantic model below represents a MongoDB collection. The collection name is the lowercase of the class name.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any

class User(BaseModel):
    email: str = Field(..., description="User email (Google OAuth primary key)")
    name: Optional[str] = Field(None, description="Full name")
    avatar: Optional[str] = Field(None, description="Profile image URL")
    is_active: bool = Field(True)

class License(BaseModel):
    user_email: str = Field(..., description="Email tied to payment and license")
    license_key: str = Field(..., description="Unique license key string")
    plan: Literal["pro-month", "pro-year"] = Field(...)
    status: Literal["active", "canceled", "expired", "trial"] = Field("active")
    provider: str = Field("dodo", description="Payment provider tag")
    current_period_end: Optional[int] = Field(None, description="Unix ts when the current period ends")

class Entitlement(BaseModel):
    user_email: str
    license_id: Optional[str] = None
    plan: Literal["free", "pro"] = "free"
    scope: Dict[str, Any] = Field(default_factory=dict)

