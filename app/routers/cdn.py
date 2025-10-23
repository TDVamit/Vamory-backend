from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File as FastAPIFile, Query, BackgroundTasks, Response
from fastapi.responses import JSONResponse
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import os
import uuid
from app.models.cdn_models import (
    CdnUploadResponse, 
    PaginatedCdnUrlsResponse, 
    PaginatedHlsStatusesResponse,
    CdnUrlResponse
)
from app.models.user import User
from app.dependencies import get_current_user, get_current_user_id, verify_secret_key
from app.database import get_cdn_upload_status_collection
from app.services.s3 import s3_service
from app.services.cdn_service import cdn_service
from app.config import settings
from app.utils import calculate_pagination_metadata, calculate_skip_from_page
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cdn", tags=["CDN"])


async def set_signed_cookies(response: Response, expiration_hours: int = 1):
    """
    Helper function to set CloudFront signed cookies in the response.
    
    Args:
        response: FastAPI Response object
        expiration_hours: Cookie expiration in hours (default: 1)
    """
    try:
        # Generate signed cookies
        signed_cookies = await cdn_service.generate_signed_cookies(
            expiration_hours=expiration_hours,
            resource_path="*"  # Allow access to all resources
        )
        
        # Calculate expiration time (must be timezone-aware UTC)
        expire_time = datetime.now(timezone.utc) + timedelta(hours=expiration_hours)
        max_age_seconds = expiration_hours * 3600
        
        # Get CDN domain from settings and ensure it doesn't include protocol
        # cdn_domain = settings.CDN_DOMAIN_NAME
        # Remove protocol if present (domain should be like "d123456.cloudfront.net")
        # cdn_domain = cdn_domain.replace("https://", "").replace("http://", "")
        
        # Set each cookie in the response
        for cookie_name, cookie_value in signed_cookies.items():
            response.set_cookie(
                key=cookie_name,
                value=cookie_value,
                max_age=max_age_seconds,
                expires=expire_time,
                path="/",
                domain=settings.CDN_DOMAIN_NAME,  # Commented out - cookie will use the response domain
                secure=True,  # Required for production HTTPS
                httponly=True,  # Prevent JavaScript access for security
                samesite="none"  # Required for cross-site requests
            )
        
        logger.info(f"Set CloudFront signed cookies with {expiration_hours}h expiration")
        
    except Exception as e:
        logger.exception(f"Error setting signed cookies: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set signed cookies: {str(e)}"
        )


def determine_file_type(content_type: str, filename: str) -> str:
    """Determine if file is a video based on content type and extension"""
    file_extension = os.path.splitext(filename)[1].lower().lstrip('.')
    
    if (content_type.startswith('video/') or 
        file_extension in settings.allowed_video_extensions):
        return "video"
    else:
        return "other"


async def process_cdn_upload(upload_id: str, s3_key: str, filename: str):
    """Background task to process CDN upload"""
    try:
        logger.info(f"Processing CDN upload: {upload_id} for file: {filename}")
        
        # Generate signed CDN URL (24 hour expiration for initial URL)
        cdn_url = await cdn_service.generate_cdn_url(s3_key, expiration_hours=24)
        
        # Update status to uploaded
        success = await cdn_service.update_upload_status(
            upload_id=upload_id,
            status="uploaded",
            cdn_url=cdn_url
        )
        
        if success:
            logger.info(f"Successfully processed CDN upload: {upload_id}")
        else:
            logger.error(f"Failed to update CDN upload status: {upload_id}")
            await cdn_service.update_upload_status(
                upload_id=upload_id,
                status="failed",
                error="Failed to update status"
            )
            
    except Exception as e:
        logger.exception(f"Error processing CDN upload {upload_id}: {e}")
        await cdn_service.update_upload_status(
            upload_id=upload_id,
            status="failed",
            error=str(e)
        )


@router.post("/upload", response_model=CdnUploadResponse)
async def upload_to_cdn(
    file: UploadFile = FastAPIFile(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    secret_key: str = Depends(verify_secret_key)
):
    """Upload file to CDN with automatic S3 bucket routing based on file type"""
    try:
        # Determine file type
        file_type = determine_file_type(file.content_type, file.filename)
        
        # Generate S3 key based on file type
        if file_type == "video":
            # Videos go to hls_source_video folder
            s3_key = f"hls_source_video/{str(uuid.uuid4())}_{file.filename}"
        else:
            # Other files go to regular bucket
            s3_key = f"cdn_uploads/{str(uuid.uuid4())}_{file.filename}"
        
        # Upload file to S3
        await file.seek(0)
        s3_url = await s3_service.upload_streaming_file(file, s3_key)
        
        # Create CDN upload status record
        upload_id = await cdn_service.create_upload_status(s3_key)
        
        # Start background task to process CDN upload
        background_tasks.add_task(process_cdn_upload, upload_id, s3_key, file.filename)
        
        return CdnUploadResponse(
            upload_id=upload_id,
            s3_key=s3_key,
            status="pending",
            message="File uploaded successfully. CDN processing started."
        )
        
    except Exception as e:
        logger.exception(f"Error uploading to CDN: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file to CDN: {str(e)}"
        )


@router.get("/urls", response_model=PaginatedCdnUrlsResponse)
async def get_cdn_urls(
    response: Response,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of URLs per page"),
    secret_key: str = Depends(verify_secret_key)
):
    """Get paginated CDN URLs (only uploaded files) with signed cookies for authorization"""
    try:
        # Set signed cookies in response (1 hour expiration)
        await set_signed_cookies(response, expiration_hours=1)
        
        cdn_urls, meta = await cdn_service.get_cdn_urls_paginated(page, per_page)
        
        # Convert to response format
        url_responses = [CdnUrlResponse(**url_data) for url_data in cdn_urls]
        
        return PaginatedCdnUrlsResponse(
            data=url_responses,
            meta=meta
        )
        
    except Exception as e:
        logger.exception(f"Error getting CDN URLs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get CDN URLs: {str(e)}"
        )


@router.get("/hls-statuses", response_model=PaginatedHlsStatusesResponse)
async def get_hls_statuses(
    response: Response,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of statuses per page"),
    status_filter: Optional[str] = Query(None, description="Filter by status: pending, uploaded, failed"),
    secret_key: str = Depends(verify_secret_key)
):
    """Get paginated HLS statuses with signed cookies for authorization"""
    try:
        # Set signed cookies in response (1 hour expiration)
        await set_signed_cookies(response, expiration_hours=1)
        
        statuses, meta = await cdn_service.get_upload_statuses_paginated(
            page=page, 
            per_page=per_page,
            status_filter=status_filter
        )
        
        return PaginatedHlsStatusesResponse(
            data=statuses,
            meta=meta
        )
        
    except Exception as e:
        logger.exception(f"Error getting HLS statuses: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get HLS statuses: {str(e)}"
        )


@router.get("/status/{upload_id}")
async def get_upload_status(
    upload_id: str,
    response: Response,
    secret_key: str = Depends(verify_secret_key)
):
    """Get specific upload status by ID with signed cookies for authorization"""
    try:
        # Set signed cookies in response (1 hour expiration)
        await set_signed_cookies(response, expiration_hours=1)
        
        upload_status = await cdn_service.get_upload_status(upload_id)
        
        if not upload_status:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload status not found"
            )
        
        return upload_status
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting upload status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get upload status: {str(e)}"
        )
