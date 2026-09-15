import boto3
import pytest
from moto import mock_aws

from config import settings
from services import object_store


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """The client is cached with lru_cache; ensure each test gets a client
    created within its own moto mock context."""
    object_store.get_client.cache_clear()
    yield
    object_store.get_client.cache_clear()


@mock_aws
def test_upload_pdf_stores_object_under_expected_key():
    boto3.client("s3", region_name=settings.aws_region).create_bucket(
        Bucket=settings.s3_bucket_name
    )

    key = object_store.upload_pdf("doc-123", "sample.pdf", b"%PDF-1.4 fake content")

    assert key == "pdfs/doc-123/sample.pdf"
    stored = boto3.client("s3", region_name=settings.aws_region).get_object(
        Bucket=settings.s3_bucket_name, Key=key
    )
    assert stored["Body"].read() == b"%PDF-1.4 fake content"


@mock_aws
def test_is_healthy_true_when_bucket_exists():
    boto3.client("s3", region_name=settings.aws_region).create_bucket(
        Bucket=settings.s3_bucket_name
    )

    assert object_store.is_healthy() is True


@mock_aws
def test_is_healthy_false_when_bucket_missing():
    assert object_store.is_healthy() is False
