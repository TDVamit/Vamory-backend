"""
Cost calculation service for storage pricing.
"""
from typing import Tuple, Dict, Any
from app.models.cost import CostStructure, CostTier, StorageCosts


class CostCalculatorService:
    """Service to calculate storage and retrieval costs"""
    
    @staticmethod
    def calculate_storage_cost(storage_gb: float, storage_costs: StorageCosts) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate cost for a given amount of storage using tiered pricing.
        
        Args:
            storage_gb: Amount of storage in GB
            storage_costs: Storage cost configuration with tiers
            
        Returns:
            Tuple of (total_cost, breakdown_dict)
        """
        if storage_gb <= 0:
            return 0.0, {}
        
        total_cost = 0.0
        breakdown = []
        remaining_gb = storage_gb
        
        # Sort tiers by min_gb to ensure correct processing order
        sorted_tiers = sorted(storage_costs.tiers, key=lambda x: x.min_gb)
        
        for tier in sorted_tiers:
            if remaining_gb <= 0:
                break
                
            # Determine the GB range for this tier
            tier_min = tier.min_gb
            tier_max = tier.max_gb if tier.max_gb is not None else float('inf')
            
            # Skip if we haven't reached this tier yet
            if storage_gb < tier_min:
                continue
                
            # Calculate how much storage falls in this tier
            gb_in_tier_start = max(0, storage_gb - remaining_gb)
            gb_in_tier_end = min(storage_gb, tier_max)
            gb_in_tier = gb_in_tier_end - max(gb_in_tier_start, tier_min)
            
            if gb_in_tier > 0:
                tier_cost = gb_in_tier * tier.cost_per_gb
                total_cost += tier_cost
                
                breakdown.append({
                    "tier_range": f"{tier_min}-{tier_max if tier_max != float('inf') else 'unlimited'}GB",
                    "gb_used": round(gb_in_tier, 2),
                    "cost_per_gb": tier.cost_per_gb,
                    "tier_cost": round(tier_cost, 4)
                })
                
                remaining_gb -= gb_in_tier
        
        return round(total_cost, 4), {
            "storage_type": storage_costs.storage_type,
            "total_gb": storage_gb,
            "total_cost": round(total_cost, 4),
            "tiers": breakdown
        }
    
    @staticmethod
    def calculate_total_costs(
        standard_gb: float,
        archive_gb: float,
        retrieval_gb: float,
        cost_structure: CostStructure
    ) -> Dict[str, Any]:
        """
        Calculate total costs for all storage types and retrieval.
        
        Args:
            standard_gb: Amount of standard storage in GB
            archive_gb: Amount of archive storage in GB
            retrieval_gb: Amount of data retrieved in GB
            cost_structure: Complete cost structure
            
        Returns:
            Dictionary with detailed cost breakdown
        """
        # Calculate standard storage costs
        standard_cost, standard_breakdown = CostCalculatorService.calculate_storage_cost(
            standard_gb, cost_structure.standard_storage
        )
        
        # Calculate archive storage costs
        archive_cost, archive_breakdown = CostCalculatorService.calculate_storage_cost(
            archive_gb, cost_structure.archive_storage
        )
        
        # Calculate retrieval costs
        retrieval_cost = retrieval_gb * cost_structure.retrieval.cost_per_gb
        retrieval_breakdown = {
            "total_gb": retrieval_gb,
            "cost_per_gb": cost_structure.retrieval.cost_per_gb,
            "total_cost": round(retrieval_cost, 4)
        }
        
        # Calculate total
        total_cost = standard_cost + archive_cost + retrieval_cost
        
        return {
            "standard_storage_cost": standard_cost,
            "archive_storage_cost": archive_cost,
            "retrieval_cost": round(retrieval_cost, 4),
            "total_cost": round(total_cost, 4),
            "breakdown": {
                "standard_storage": standard_breakdown,
                "archive_storage": archive_breakdown,
                "retrieval": retrieval_breakdown
            }
        }


# Global service instance
cost_calculator_service = CostCalculatorService()



