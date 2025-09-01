"""
Billing utilities for file size calculations.
"""


def apply_minimum_file_size(actual_size: int) -> int:
    """
    Apply minimum file size of 128KB for billing/storage tracking purposes.
    
    Args:
        actual_size: The actual file size in bytes
        
    Returns:
        The size to use for billing (minimum 128KB)
    """
    MIN_SIZE = 128 * 1024  # 128KB in bytes
    return max(actual_size, MIN_SIZE)


def calculate_total_billing_size(file_size: int, thumbnail_size: int = 0) -> int:
    """
    Calculate total billing size for a file including its thumbnail.
    
    Args:
        file_size: The actual file size in bytes
        thumbnail_size: The thumbnail size in bytes (default 0 if no thumbnail)
        
    Returns:
        The total billing size (file + thumbnail, each with minimum 128KB)
    """
    file_billing_size = apply_minimum_file_size(file_size)
    thumbnail_billing_size = apply_minimum_file_size(thumbnail_size) if thumbnail_size > 0 else 0
    
    return file_billing_size + thumbnail_billing_size
