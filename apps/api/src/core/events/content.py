import asyncio
import logging
import os

from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


def _ensure_s3_bucket_sync() -> None:
    from src.services.courses.transfer.storage_utils import (
        get_s3_bucket_name,
        get_storage_client,
        is_s3_enabled,
    )

    if not is_s3_enabled():
        return
    client = get_storage_client()
    if client is None:
        return
    bucket = get_s3_bucket_name()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchBucket", "NotFound"}:
            logger.info("Creating object storage bucket '%s'...", bucket)
            try:
                client.create_bucket(Bucket=bucket)
            except ClientError as create_exc:
                create_code = str(create_exc.response.get("Error", {}).get("Code", ""))
                if create_code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    raise
        else:
            logger.warning("Could not verify object storage bucket '%s': %s", bucket, exc)
    except BotoCoreError as exc:
        logger.warning("Could not reach object storage for bucket '%s': %s", bucket, exc)


async def check_content_directory():
    if not os.path.exists("content"):
        # create folder for activity / local staging
        logger.info("Creating content directory...")
        os.makedirs("content")
    await asyncio.to_thread(_ensure_s3_bucket_sync)
