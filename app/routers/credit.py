from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
from app.dependencies import get_current_user
from app.models.user import User
from app.services.currency_helper import convert, currencies
from pydantic import BaseModel

router = APIRouter(prefix="/credit", tags=["Credit"])

class ExchangeRateResponse(BaseModel):
    base_currency: str
    target_currency: str
    amount: float
    converted_amount: float
    rate: float
    base_currency_info: dict
    target_currency_info: dict

@router.get("/exchange-rate", response_model=ExchangeRateResponse)
async def get_exchange_rate(
    base: str = Query(..., description="Base currency code (e.g., 'USD')"),
    amount: float = Query(..., description="Amount to convert", gt=0),
    target: str = Query(default="USD", description="Target currency code (e.g., 'EUR')"),
    current_user: User = Depends(get_current_user)
):
    """
    Get current exchange rate and convert amount from base currency to target currency.
    Requires authentication.
    """
    # Normalize currency codes to lowercase
    base_lower = base.lower()
    target_lower = target.lower()
    
    # Validate currency codes
    if base_lower not in currencies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base currency code: {base}. Supported currencies: {list(currencies.keys())}"
        )
    
    if target_lower not in currencies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target currency code: {target}. Supported currencies: {list(currencies.keys())}"
        )
    
    try:
        # Convert the amount
        converted_amount = await convert(base, amount, target)
        
        if converted_amount is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unable to fetch exchange rate for {base} to {target}"
            )
        
        # Calculate the rate
        rate = converted_amount / amount
        
        return ExchangeRateResponse(
            base_currency=base.upper(),
            target_currency=target.upper(),
            amount=amount,
            converted_amount=converted_amount,
            rate=rate,
            base_currency_info=currencies[base_lower],
            target_currency_info=currencies[target_lower]
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching exchange rate: {str(e)}"
        )
