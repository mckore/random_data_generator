from functools import lru_cache
from typing import Dict, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import settings


@lru_cache(maxsize=1)
def get_client():
    """boto3 client using the default credential chain (env vars, ~/.aws,
    AWS_PROFILE, or IAM role) - no secrets are configured here."""
    return boto3.client("s3", region_name=settings.aws_region)


def _sse_kwargs() -> Dict[str, str]:
    """Server-side encryption parameters applied to every put."""
    if settings.s3_sse_mode == "aws:kms":
        return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": settings.s3_kms_key_id}
    return {"ServerSideEncryption": "AES256"}


def pdf_key(doc_id: str, filename: str) -> str:
    return f"pdfs/{doc_id}/{filename}"


def vault_key(doc_id: str) -> str:
    return f"pdfs/{doc_id}/pii_vault.json"


def put_object(key: str, body: bytes, content_type: str) -> str:
    get_client().put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=body,
        ContentType=content_type,
        **_sse_kwargs(),
    )
    return key


def get_object(key: str) -> Optional[bytes]:
    try:
        response = get_client().get_object(Bucket=settings.s3_bucket_name, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    return response["Body"].read()


def delete_object(key: str) -> None:
    get_client().delete_object(Bucket=settings.s3_bucket_name, Key=key)


def upload_pdf(doc_id: str, filename: str, pdf_bytes: bytes) -> str:
    return put_object(pdf_key(doc_id, filename), pdf_bytes, "application/pdf")


def delete_document_objects(*keys: Optional[str]) -> None:
    for key in keys:
        if key:
            delete_object(key)


def is_healthy() -> bool:
    try:
        get_client().head_bucket(Bucket=settings.s3_bucket_name)
        return True
    except (BotoCoreError, ClientError):
        return False
