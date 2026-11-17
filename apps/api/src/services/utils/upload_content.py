import asyncio
import logging
import shutil
from typing import AsyncIterator, BinaryIO, Literal, Optional, Union
import boto3
import botocore.config
from botocore.exceptions import BotoCoreError, ClientError
import os
from fastapi import HTTPException, UploadFile
from config.config import get_learnhouse_config
from src.security.file_validation import validate_upload_stream
from src.services.utils.video_processing import ensure_faststart

logger = logging.getLogger(__name__)


_CONTENT_ROOT = "content"


def _safe_content_path(*parts: str) -> str:
    """Build a path under the content root and confirm (via realpath +
    commonpath) that user-supplied parts can't escape it. Returns the
    canonicalized absolute path; raises HTTP 400 on any traversal attempt."""
    for part in parts:
        if part is None or "\x00" in part or ".." in str(part).replace("\\", "/").split("/"):
            raise HTTPException(status_code=400, detail="Invalid file path")
    base_real = os.path.realpath(_CONTENT_ROOT)
    full_real = os.path.realpath(os.path.join(_CONTENT_ROOT, *parts))
    try:
        contained = full_real == base_real or os.path.commonpath([base_real, full_real]) == base_real
    except ValueError:
        contained = False
    if not contained:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return full_real


def _write_body(dest: BinaryIO, body: Union[bytes, BinaryIO]) -> None:
    if isinstance(body, (bytes, bytearray)):
        dest.write(body)
    else:
        shutil.copyfileobj(body, dest, 1024 * 1024)


def ensure_directory_exists(directory: str):
    if not os.path.exists(directory):
        os.makedirs(directory)


async def upload_file(
    file: UploadFile,
    directory: str,
    type_of_dir: Literal["orgs", "users"],
    uuid: str,
    allowed_types: list[str],
    filename_prefix: str,
    max_size: Optional[int] = None,
) -> str:
    """
    Secure file upload with validation.
    
    Args:
        file: The uploaded file
        directory: Target directory (e.g., "logos", "avatars")
        type_of_dir: "orgs" or "users"
        uuid: Organization or user UUID
        allowed_types: List of allowed file types ('image', 'video', 'document')
        filename_prefix: Prefix for the generated filename
        max_size: Maximum file size in bytes (optional)
        
    Returns:
        The saved filename
    """
    from uuid import uuid4
    from src.security.file_validation import get_safe_filename
    
    # Validate the file. The body stays in the upload's spooled temp file and is
    # copied to storage in chunks, so multi-GB uploads never sit in memory.
    content_type = validate_upload_stream(file, allowed_types, max_size)

    filename = get_safe_filename(
        file.filename, f"{uuid4()}_{filename_prefix}", content_type=content_type
    )
    
    # Save the file
    await upload_content(
        directory=directory,
        type_of_dir=type_of_dir,
        uuid=uuid,
        file_binary=file.file,
        file_and_format=filename,
        allowed_formats=None,  # Already validated
    )
    
    return filename


async def upload_content(
    directory: str,
    type_of_dir: Literal["orgs", "users"],
    uuid: str,  # org_uuid or user_uuid
    file_binary: Union[bytes, BinaryIO],
    file_and_format: str,
    allowed_formats: Optional[list[str]] = None,
):
    # Get Learnhouse Config
    learnhouse_config = get_learnhouse_config()

    file_format = file_and_format.split(".")[-1].strip().lower()

    # Get content delivery method
    content_delivery = learnhouse_config.hosting_config.content_delivery.type

    # Check if format file is allowed
    if allowed_formats:
        if file_format not in allowed_formats:
            raise HTTPException(
                status_code=400,
                detail=f"File format {file_format} not allowed",
            )

    # Canonicalize + containment-check the destination so a crafted filename or
    # directory can't escape the content root (prevents path traversal).
    safe_dir = _safe_content_path(type_of_dir, uuid, directory)
    ensure_directory_exists(safe_dir)
    safe_path = _safe_content_path(type_of_dir, uuid, directory, file_and_format)

    if content_delivery == "filesystem":
        # upload file to server
        with open(safe_path, "wb") as f:
            _write_body(f, file_binary)
        # Move the MP4 index atom to the front so long videos stream/seek
        # smoothly over HTTP (no-op for non-MP4 and when ffmpeg is absent).
        # Runs in a thread; the ffmpeg subprocess must not block the event loop.
        await asyncio.to_thread(ensure_faststart, safe_path)

    elif content_delivery == "s3api":
        s3 = boto3.client(
            "s3",
            endpoint_url=learnhouse_config.hosting_config.content_delivery.s3api.endpoint_url,
            config=botocore.config.Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}),
        )

        bucket_name = learnhouse_config.hosting_config.content_delivery.s3api.bucket_name or "learnhouse-media"
        local_path = safe_path
        # The S3 key stays a clean relative content path.
        s3_key = f"content/{type_of_dir}/{uuid}/{directory}/{file_and_format}"

        # Write to local temp file for S3 upload
        with open(local_path, "wb") as f:
            _write_body(f, file_binary)

        # Move the MP4 index atom to the front before uploading so long videos
        # stream/seek smoothly from R2 (no-op for non-MP4 and when ffmpeg is
        # absent). Done on the temp file so the uploaded object is faststart.
        # Threaded; the ffmpeg subprocess must not block the event loop.
        await asyncio.to_thread(ensure_faststart, local_path)

        try:
            await asyncio.to_thread(s3.upload_file, local_path, bucket_name, s3_key)
            await asyncio.to_thread(s3.head_object, Bucket=bucket_name, Key=s3_key)
            logger.debug("S3 upload successful: %s", s3_key)
        except (ClientError, BotoCoreError) as e:
            logger.error("S3 upload failed: %s", e)
            raise HTTPException(status_code=500, detail="File upload to storage failed")
        finally:
            # Clean up local temp file after S3 upload
            try:
                os.remove(local_path)
            except OSError as cleanup_err:
                logger.error("Failed to clean up temp file %s: %s", local_path, cleanup_err)


async def read_content(
    directory: str,
    type_of_dir: Literal["orgs", "users"],
    uuid: str,  # org_uuid or user_uuid
    file_and_format: str,
) -> bytes:
    """Read raw bytes for a stored content file (filesystem or S3/R2).

    The symmetric counterpart of :func:`upload_content`. Used e.g. to feed a
    previously-generated image back into the model for iterative editing without
    a client round-trip (no CORS/credentials concerns). Path parts are
    traversal-guarded; raises HTTP 404 when the file is missing.
    """
    # Defense in depth: the filesystem branch is guarded by _safe_content_path,
    # but the S3 branch builds the key by string interpolation, so reject any
    # separators/traversal in the caller-supplied filename for both.
    if (
        not file_and_format
        or "/" in file_and_format
        or "\\" in file_and_format
        or ".." in file_and_format
        or "\x00" in file_and_format
    ):
        raise HTTPException(status_code=400, detail="Invalid file name")

    learnhouse_config = get_learnhouse_config()
    content_delivery = learnhouse_config.hosting_config.content_delivery.type

    if content_delivery == "s3api":
        s3 = boto3.client(
            "s3",
            endpoint_url=learnhouse_config.hosting_config.content_delivery.s3api.endpoint_url,
            config=botocore.config.Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}),
        )
        bucket_name = learnhouse_config.hosting_config.content_delivery.s3api.bucket_name or "learnhouse-media"
        s3_key = f"content/{type_of_dir}/{uuid}/{directory}/{file_and_format}"
        try:
            resp = await asyncio.to_thread(s3.get_object, Bucket=bucket_name, Key=s3_key)
            return await asyncio.to_thread(resp["Body"].read)
        except (ClientError, BotoCoreError) as e:
            logger.error("S3 read failed: %s", e)
            raise HTTPException(status_code=404, detail="File not found")

    # filesystem
    safe_path = _safe_content_path(type_of_dir, uuid, directory, file_and_format)
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404, detail="File not found")
    return await asyncio.to_thread(_read_file_bytes, safe_path)


async def open_content_stream(
    directory: str,
    type_of_dir: Literal["orgs", "users"],
    uuid: str,
    file_and_format: str,
    *,
    chunk_size: int = 64 * 1024,
) -> AsyncIterator[bytes]:
    """Open a private storage object for authenticated server-side streaming.

    Unlike the media redirect helper, this never returns or logs a presigned
    URL. Opening happens before the response is sent so missing objects can be
    reported as an HTTP error instead of a truncated successful download.
    """
    normalized_directory = directory.replace("\\", "/")
    directory_parts = normalized_directory.split("/")
    if (
        type_of_dir not in {"orgs", "users"}
        or not uuid
        or any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in uuid)
        or not normalized_directory
        or normalized_directory.startswith("/")
        or any(part in {"", ".", ".."} or "\x00" in part for part in directory_parts)
        or not file_and_format
        or "/" in file_and_format
        or "\\" in file_and_format
        or ".." in file_and_format
        or "\x00" in file_and_format
        or len(file_and_format) > 255
        or any(not (char.isascii() and (char.isalnum() or char in "._-")) for char in file_and_format)
        or not 1 <= chunk_size <= 1024 * 1024
    ):
        raise HTTPException(status_code=400, detail="Invalid private storage reference")

    directory = normalized_directory
    learnhouse_config = get_learnhouse_config()
    content_delivery = learnhouse_config.hosting_config.content_delivery.type
    if content_delivery == "s3api":
        s3 = boto3.client(
            "s3",
            endpoint_url=learnhouse_config.hosting_config.content_delivery.s3api.endpoint_url,
            config=botocore.config.Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 2},
            ),
        )
        bucket_name = learnhouse_config.hosting_config.content_delivery.s3api.bucket_name or "learnhouse-media"
        s3_key = f"content/{type_of_dir}/{uuid}/{directory}/{file_and_format}"
        try:
            response = await asyncio.to_thread(s3.get_object, Bucket=bucket_name, Key=s3_key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "NotFound", "404"}:
                raise HTTPException(status_code=404, detail="File not found") from error
            raise HTTPException(status_code=503, detail="Private file storage is unavailable") from error
        except BotoCoreError as error:
            raise HTTPException(status_code=503, detail="Private file storage is unavailable") from error
        body = response["Body"]

        async def s3_chunks() -> AsyncIterator[bytes]:
            try:
                while True:
                    chunk = await asyncio.to_thread(body.read, chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(body.close)

        return s3_chunks()

    if content_delivery != "filesystem":
        raise HTTPException(status_code=503, detail="Private file storage is unavailable")

    safe_path = _safe_content_path(type_of_dir, uuid, directory, file_and_format)
    try:
        handle = await asyncio.to_thread(open, safe_path, "rb")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="File not found") from error
    except OSError as error:
        raise HTTPException(status_code=503, detail="Private file storage is unavailable") from error

    async def filesystem_chunks() -> AsyncIterator[bytes]:
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    return filesystem_chunks()


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()
