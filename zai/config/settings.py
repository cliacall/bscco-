import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

BSC_CHAIN_ID = 56
DEXSCREENER_CHAIN = "bsc"
GECKO_NETWORK = "bsc"
API_TIMEOUT = 15
SCAN_INTERVAL = 10  # four.meme 新币 GMGN 扫描间隔（秒）
VOLUME_THRESHOLD = 1000


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    ai_provider: str
    deepseek_api_key: str
    zai_api_key: str
    cobo_api_key: str
    cobo_wallet_id: str
    bsc_rpc_url: str
    wallet_address: str = ""
    notify_chat_id: str = ""
    web_port: int = 8888
    volume_threshold: float = VOLUME_THRESHOLD
    scan_interval: int = SCAN_INTERVAL
    api_timeout: int = API_TIMEOUT
    gmgn_pull_interval: int = 300
    gmgn_api_key: str = ""

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    zai_base_url: str = "https://api.z.ai/api/paas/v4/"
    zai_model: str = "glm-5.1"
    cobo_api_url: str = "https://api.agenticwallet.cobo.com"

    @property
    def ai_label(self) -> str:
        return "ZAI" if self.ai_provider == "zai" else "DeepSeek"

    @property
    def ai_api_key(self) -> str:
        return self.zai_api_key if self.ai_provider == "zai" else self.deepseek_api_key

    @property
    def ai_base_url(self) -> str:
        return self.zai_base_url if self.ai_provider == "zai" else self.deepseek_base_url

    @property
    def ai_model(self) -> str:
        return self.zai_model if self.ai_provider == "zai" else self.deepseek_model

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_api_key)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def notify_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.notify_chat_id)

    @property
    def deepseek_configured(self) -> bool:
        return self.ai_configured

    @property
    def cobo_configured(self) -> bool:
        from plugins.cobo_local import is_cobo_ready
        return is_cobo_ready(self.cobo_api_key, self.cobo_wallet_id)


@lru_cache
def get_settings() -> Settings:
    ai_provider = os.getenv("AI_PROVIDER", "zai").strip().lower()
    if ai_provider not in {"zai", "deepseek"}:
        ai_provider = "zai"
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        notify_chat_id=os.getenv("TELEGRAM_NOTIFY_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")),
        ai_provider=ai_provider,
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        zai_api_key=os.getenv("ZAI_API_KEY", ""),
        cobo_api_key=os.getenv("COBO_API_KEY", ""),
        cobo_wallet_id=os.getenv("COBO_WALLET_ID", ""),
        bsc_rpc_url=os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org"),
        wallet_address=os.getenv("WALLET_ADDRESS", "").strip().lower(),
        web_port=int(os.getenv("WEB_PORT", "8888")),
        volume_threshold=float(os.getenv("VOLUME_THRESHOLD", str(VOLUME_THRESHOLD))),
        scan_interval=int(os.getenv("SCAN_INTERVAL", str(SCAN_INTERVAL))),
        gmgn_pull_interval=int(os.getenv("GMGN_PULL_INTERVAL", "300")),
        gmgn_api_key=os.getenv("GMGN_API_KEY", "").strip(),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        zai_base_url=os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4/"),
        zai_model=os.getenv("ZAI_MODEL", "glm-5.1"),
    )
