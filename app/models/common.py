from pydantic import BaseModel, Field
from typing import Optional, List, Any


class PaginationMetadata(BaseModel):
    """Pagination metadata for list responses"""
    total_count: int = Field(..., description="Total number of items available")
    page_count: int = Field(..., description="Total number of pages")
    current_page: int = Field(..., description="Current page number (1-based)")
    per_page: int = Field(..., description="Number of items per page")
    has_next: bool = Field(..., description="Whether there is a next page")
    has_prev: bool = Field(..., description="Whether there is a previous page")
    next_page: Optional[int] = Field(None, description="Next page number if available")
    prev_page: Optional[int] = Field(None, description="Previous page number if available")


class PaginatedResponse(BaseModel):
    """Generic paginated response"""
    data: List[Any] = Field(..., description="List of items")
    meta: PaginationMetadata = Field(..., description="Pagination metadata") 