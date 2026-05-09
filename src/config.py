from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    scrape_url: str = "https://arena.ai/leaderboard/text/overall-no-style-control"
    request_timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 5.0
    user_agent: str = "ArenaLeaderboardTracker/1.0"

    resend_api_key: str = ""
    alert_recipient: str = "shyamvora91@gmail.com"
    alert_from_email: str = "onboarding@resend.dev"
    alert_rank_threshold: int = 3
    dashboard_url: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
