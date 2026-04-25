from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    nekro_server_url: str = "http://localhost:8080"
    nekro_access_key: str = ""
    webchat_platform: str = "webchat"
    webchat_channel_id: str = "webchat_main"
    webchat_channel_name: str = "WebChat"
    webchat_user_id: str = "web_user"
    webchat_user_name: str = "Web User"
    webchat_bot_name: str = "NekroAgent"
    webchat_database_url: str = "sqlite+aiosqlite:///./data/webchat.db"
    webchat_jwt_secret: str = "nekro-webchat-change-me-in-production"
    webchat_jwt_expire_minutes: int = 10080  # 默认 7 天

    max_upload_size_mb: int = 20  # 限制单文件上传最大大小(MB)
    cleanup_max_file_age_days: int = 7  # 自动清理多少天前的文件
    cleanup_max_total_size_mb: int = 500  # uploads 文件夹的总容量上限(MB)，超出时按时间清理最旧文件

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
