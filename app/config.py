import os
from typing import List, Union
from pydantic_settings import BaseSettings
from pydantic import field_validator
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # Database
    mongodb_url: str
    database_name: str = "gallery_db"
    
    # JWT Authentication
    secret_key: str 
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    
    # AWS S3 Configuration
    aws_access_key_id: str 
    aws_secret_access_key: str 
    aws_region: str 
    s3_bucket_name: str 

    drive_api_key:str
    
    # Application Settings
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Upload Settings
    max_file_size: str = "100MB"
    max_video_size: str = "10GB"
    allowed_image_extensions: Union[str, List[str]] = "jpg,jpeg,png,gif,webp,bmp,tiff"
    allowed_video_extensions: Union[str, List[str]] = "mp4,avi,mov,wmv,flv,webm,mkv,m4v"
    thumbnail_size: int = 300
    thumbnail_quality: int = 85

    GEMINI_API_KEY:str
    GEMINI_VISION_MODEL:str
    OPENAI_API_KEY:str
    OPENAI_EMBEDDING_MODEL:str
    speech_is_cheap_api_key:str
    qdrant_api_key:str
    qdrant_url:str
    AUTH0_DOMAIN:str
    API_AUDIENCE:str
    AUTH0_CLIENT_ID:str
    AUTH0_CLIENT_SECRET:str
    AUTH0_CANONICAL_DOMAIN:str
    AUTH0_API_CLIENT_ID:str
    PUBLIC_SECRET_KEY:str
    MAILGUN_API_KEY:str
    MAILGUN_BASE_URL:str
    FROM_NAME:str
    EMAIL_DOMAIN:str
    EMAIL_NAME:str
    SEND_DOMAIN:str

    CDN_KEY_GROUP_ID:str
    CDN_PRIVATE_KEY_PATH:str
    CDN_DOMAIN_NAME:str
    SQS_URL:str

    PUBLIC_SECRET_KEY:str
    redis_url:str

    @field_validator('allowed_image_extensions', 'allowed_video_extensions')
    @classmethod
    def parse_list_from_string(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(',') if item.strip()]
        return v
    
    class Config:
        env_file = ".env"
        case_sensitive = False

    def get_max_file_size_bytes(self) -> int:
        """Convert max_file_size string to bytes"""
        size_str = self.max_file_size.upper()
        if size_str.endswith('MB'):
            return int(size_str[:-2]) * 1024 * 1024
        elif size_str.endswith('GB'):
            return int(size_str[:-2]) * 1024 * 1024 * 1024
        elif size_str.endswith('KB'):
            return int(size_str[:-2]) * 1024
        else:
            return int(size_str)

    def get_max_video_size_bytes(self) -> int:
        """Convert max_video_size string to bytes"""
        size_str = self.max_video_size.upper()
        if size_str.endswith('MB'):
            return int(size_str[:-2]) * 1024 * 1024
        elif size_str.endswith('GB'):
            return int(size_str[:-2]) * 1024 * 1024 * 1024
        elif size_str.endswith('KB'):
            return int(size_str[:-2]) * 1024
        else:
            return int(size_str)


settings = Settings() 