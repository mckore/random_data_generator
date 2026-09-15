import boto3
import pytest
from moto import mock_aws

import config
from config import settings
from services import object_store


@pytest.fixture(autouse=True)
def _fresh_client():
    """The client is cached with lru_cache; ensure each test gets a client
    created within its own moto mock context."""
    object_store.get_client.cache_clear()
    yield
    object_store.get_client.cache_clear()


def _s3():
    return boto3.client("s3", region_name=settings.aws_region)


def _bucket():
    _s3().create_bucket(Bucket=settings.s3_bucket_name)


@mock_aws
def test_upload_pdf_stores_object_under_expected_key_with_sse():
    _bucket()

    key = object_store.upload_pdf("doc-123", "sample.pdf", b"%PDF-1.4 fake content")

    assert key == "pdfs/doc-123/sample.pdf"
    obj = _s3().get_object(Bucket=settings.s3_bucket_name, Key=key)
    assert obj["Body"].read() == b"%PDF-1.4 fake content"
    assert obj["ServerSideEncryption"] == "AES256"
    assert obj["ContentType"] == "application/pdf"


@mock_aws
def test_put_uses_kms_when_configured(monkeypatch):
    _bucket()
    monkeypatch.setattr(config.settings, "s3_sse_mode", "aws:kms")
    monkeypatch.setattr(config.settings, "s3_kms_key_id", "alias/pdf-rag-test")

    key = object_store.put_object("pdfs/x/vault.json", b"{}", "application/json")

    head = _s3().head_object(Bucket=settings.s3_bucket_name, Key=key)
    assert head["ServerSideEncryption"] == "aws:kms"
    assert "pdf-rag-test" in head["SSEKMSKeyId"]


@mock_aws
def test_get_object_round_trip_and_missing():
    _bucket()
    object_store.put_object("k", b"hello", "text/plain")

    assert object_store.get_object("k") == b"hello"
    assert object_store.get_object("missing") is None


@mock_aws
def test_delete_document_objects_removes_pdf_and_vault():
    _bucket()
    pdf = object_store.upload_pdf("doc-1", "a.pdf", b"%PDF")
    vault = object_store.put_object(object_store.vault_key("doc-1"), b"{}", "application/json")

    object_store.delete_document_objects(pdf, vault, None)

    assert object_store.get_object(pdf) is None
    assert object_store.get_object(vault) is None


@mock_aws
def test_is_healthy_true_when_bucket_exists():
    _bucket()
    assert object_store.is_healthy() is True


@mock_aws
def test_is_healthy_false_when_bucket_missing():
    assert object_store.is_healthy() is False
