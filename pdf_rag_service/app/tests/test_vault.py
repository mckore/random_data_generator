import boto3
import pytest
from moto import mock_aws

from config import settings
from services import object_store, vault

TOKENS = {
    "<PERSON_1>": {"type": "PERSON", "value": "Jane Doe"},
    "<ADDRESS_1>": {"type": "ADDRESS", "value": "123 Main Street, Springfield, IL 62704"},
}


@pytest.fixture(autouse=True)
def _fresh_client():
    object_store.get_client.cache_clear()
    yield
    object_store.get_client.cache_clear()


def _bucket():
    boto3.client("s3", region_name=settings.aws_region).create_bucket(Bucket=settings.s3_bucket_name)


@mock_aws
def test_save_and_load_round_trip():
    _bucket()
    key = vault.save_vault("doc-1", TOKENS)

    assert key == "pdfs/doc-1/pii_vault.json"
    assert vault.load_vault("doc-1") == TOKENS


@mock_aws
def test_vault_object_is_server_side_encrypted():
    _bucket()
    key = vault.save_vault("doc-1", TOKENS)

    head = boto3.client("s3", region_name=settings.aws_region).head_object(
        Bucket=settings.s3_bucket_name, Key=key
    )
    assert head["ServerSideEncryption"] == "AES256"
    assert head["ContentType"] == "application/json"


@mock_aws
def test_load_missing_vault_returns_none():
    _bucket()
    assert vault.load_vault("nope") is None


@mock_aws
def test_delete_vault_removes_object():
    _bucket()
    vault.save_vault("doc-1", TOKENS)
    vault.delete_vault("doc-1")
    assert vault.load_vault("doc-1") is None


def test_build_vault_shape():
    built = vault.build_vault("doc-1", TOKENS)
    assert built["version"] == vault.VAULT_VERSION
    assert built["doc_id"] == "doc-1"
    assert built["tokens"] == TOKENS
    assert "created_at" in built
