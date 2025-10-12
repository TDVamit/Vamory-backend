import os
import asyncio
import json
import logging
from typing import Optional, Dict
from datetime import datetime

import aiobotocore.session
from app.database import get_cdn_upload_status_collection, ensure_cdn_upload_status_collection
from app.config import settings

# Configure logging for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Don't add custom handler - let uvicorn handle output
# Just set the level so our INFO messages get through
logger.propagate = True  # Let uvicorn's handler display the logs

# ========== Configuration ==========

REGION = settings.aws_region
SQS_QUEUE_URL = settings.SQS_URL
POLL_WAIT_TIME = 20  # seconds for long polling (integer required)
MAX_MESSAGES = 5  # integer required
VISIBILITY_TIMEOUT = 60  # integer required

# ========== Database Operations ==========


# ========== SQS Async Wrapper ==========

async def get_sqs_client():
    session = aiobotocore.session.get_session()
    return session.create_client('sqs', region_name=REGION)


async def receive_messages(sqs_client) -> Optional[list]:
    """
    Retrieve messages from SQS queue via long polling.
    Returns list of messages (dictionaries), or [] if none.
    """
    try:
        resp = await sqs_client.receive_message(
            QueueUrl=SQS_QUEUE_URL,
            MaxNumberOfMessages=MAX_MESSAGES,
            WaitTimeSeconds=POLL_WAIT_TIME,
            VisibilityTimeout=VISIBILITY_TIMEOUT,
            MessageAttributeNames=['All'],
        )
        msgs = resp.get("Messages", [])
        return msgs
    except Exception as e:
        logger.exception("receive_messages error: %s", e)
        return []


async def delete_message(sqs_client, receipt_handle: str):
    """
    Deletes one message from SQS using its receipt handle.
    """
    try:
        await sqs_client.delete_message(
            QueueUrl=SQS_QUEUE_URL,
            ReceiptHandle=receipt_handle
        )
    except Exception as e:
        logger.exception("delete_message error: %s", e)


# ========== Processing Logic ==========

async def   process_message_body(body: dict):
    """
    Process one message body (parsed JSON).
    Update HLS conversion status in database.
    Only updates if job_id is found in the database.
    Handles both direct EventBridge messages and SNS-wrapped messages.
    """
    logger.info("Received message body: %s", body)
    
    # Check if this is an SNS notification wrapping the actual message
    msg_type = body.get("Type")
    s3_bucket = body.get("s3_bucket")
    if msg_type and msg_type == "Notification" and "Message" in body:
        # SNS-wrapped message - unwrap it
        try:
            message_str = body.get("Message", "{}")
            body = json.loads(message_str)
            logger.debug("Unwrapped SNS message: %s", body)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse SNS Message field: %s", e)
            return False

    if s3_bucket:
        detail = body
    else:
        detail = body.get("detail", {})
        
    job_id = detail.get("jobId") 
    status = detail.get("status")
    error = detail.get("error")
    s3_key = detail.get("s3_key")

    logger.info("Processing HLS status update: job_id=%s, status=%s, s3_key=%s, error=%s", 
                job_id, status, s3_key, error)

    # Must have s3_key to query the CDN upload status collection
    if s3_key:
        query = {"s3_key": s3_key}
    elif job_id:
        query = {"job_id": job_id}
    else: 
        logger.error("No valid query criteria found: job_id=%s, s3_key=%s", job_id, s3_key)
        return False

    logger.info("Querying by %s:", query)

    # Build update values - only include fields that exist in CdnUploadStatus model
    update_values = {
        "updated_at": datetime.utcnow()
    }
    
    if status:
        update_values["status"] = status
        # If status is uploaded/complete, set uploaded_at timestamp
        if status.lower() in ["uploaded", "complete", "completed","started"]:
            update_values["uploaded_at"] = datetime.utcnow()
    
    if error:
        update_values["error"] = error
    
    if job_id:
        update_values["job_id"] = job_id

    try:
        # Get the CDN upload status collection
        collection = await get_cdn_upload_status_collection()
        if collection is None:
            logger.error("Failed to get CDN upload status collection")
            return False
        
        result = await collection.find_one_and_update(
            query,
            {"$set": update_values}
        )
        
        if result:
            logger.info("✅ Updated CDN upload status: job_id=%s, status=%s, s3_key=%s", job_id, status, s3_key)
            return True
        else:
            logger.warning("⚠️ No CDN upload status record found for query: %s. The record may not exist yet.", query)
            logger.info("💡 Tip: Ensure the CDN upload record is created in DB before the status update arrives.")
            return False
    except Exception as e:
        logger.exception("Database update failed: %s", e)
        return False


# ========== Initialization ==========

async def initialize_cdn_upload_status():
    """Initialize CDN upload status collection"""
    try:
        success = await ensure_cdn_upload_status_collection()
        if success:
            logger.info("✓ CDN upload status collection initialized successfully")
        else:
            logger.error("✗ Failed to initialize CDN upload status collection")
        return success
    except Exception as e:
        logger.exception("Error initializing CDN upload status: %s", e)
        return False


# ========== Worker Loop ==========

async def worker_loop():
    """
    Main worker loop that polls SQS queue and processes messages.
    Runs indefinitely until cancelled.
    """
    logger.info("🚀 Starting HLS Queue Listener")
    logger.info("📡 Polling SQS queue: %s", SQS_QUEUE_URL)
    
    # Initialize CDN upload status collection
    init_success = await initialize_cdn_upload_status()
    if not init_success:
        logger.error("❌ Failed to initialize. Exiting worker loop.")
        return
    
    try:
        async with await get_sqs_client() as sqs_client:
            message_count = 0
            logger.info("✅ SQS client connected. Waiting for messages...")
            
            while True:
                try:
                    messages = await receive_messages(sqs_client)
                    
                    if not messages:
                        # Long polling returns empty list when no messages
                        # No need to log or sleep, just continue
                        continue

                    logger.info("📥 Received %d message(s)", len(messages))

                    for msg in messages:
                        receipt = msg.get("ReceiptHandle")
                        body_text = msg.get("Body")

                        try:
                            body = json.loads(body_text)
                        except json.JSONDecodeError:
                            logger.error("❌ Invalid JSON body: %s", body_text[:100])
                            await delete_message(sqs_client, receipt)
                            continue

                        processed = await process_message_body(body)
                        if processed:
                            await delete_message(sqs_client, receipt)
                            message_count += 1
                            logger.info("✅ Message processed successfully (total: %d)", message_count)
                        else:
                            logger.warning("⚠️ Failed to process message, will retry after visibility timeout")
                
                except asyncio.CancelledError:
                    logger.info("🛑 Worker loop cancelled, shutting down gracefully...")
                    raise
                except Exception as e:
                    logger.exception("❌ Unexpected error in worker loop: %s", e)
                    # Wait before retrying to avoid tight error loop
                    await asyncio.sleep(5)
    
    except asyncio.CancelledError:
        logger.info("✅ HLS Queue Listener stopped")
        raise
    except Exception as e:
        logger.exception("❌ Fatal error in worker loop: %s", e)


if __name__ == "__main__":
    asyncio.run(worker_loop())
