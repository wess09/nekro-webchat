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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
