from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import settings


@lru_cache(maxsize=1)
def get_client():
    """boto3 client using the default credential chain (env vars, ~/.aws,
    AWS_PROFILE, or IAM role) - no secrets are configured here."""
    return boto3.client("s3", region_name=settings.aws_region)


def upload_pdf(doc_id: str, filename: str, pdf_bytes: bytes) -> str:
    key = f"pdfs/{doc_id}/{filename}"
    get_client().put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=pdf_bytes,
        ContentType="application/pdf",
    )
    return key


def is_healthy() -> bool:
    try:
        get_client().head_bucket(Bucket=settings.s3_bucket_name)
        return True
    except (BotoCoreError, ClientError):
        return False
