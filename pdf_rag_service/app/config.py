from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "pdf_chunks"

    s3_bucket_name: str = "jm-dap-dev-landing-o-use1"
    aws_region: str = "us-east-1"

    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 384

    chunk_size: int = 1000
    chunk_overlap: int = 150

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)


settings = Settings()
