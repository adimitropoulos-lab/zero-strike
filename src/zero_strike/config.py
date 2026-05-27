from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")

    gamma_url: str = os.getenv("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
    clob_url: str = os.getenv("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")
    data_url: str = os.getenv("POLYMARKET_DATA_URL", "https://data-api.polymarket.com")
    subgraph_url: str = os.getenv(
        "POLYMARKET_SUBGRAPH_URL",
        "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/"
        "subgraphs/positions-subgraph/0.0.7/gn",
    )

    rss_feeds: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            f.strip()
            for f in os.getenv(
                "NEWS_RSS_FEEDS",
                "https://feeds.bbci.co.uk/news/world/rss.xml,"
                "https://feeds.apnews.com/rss/apf-topnews",
            ).split(",")
            if f.strip()
        )
    )

    bankroll: float = float(os.getenv("BANKROLL_USDC", "10000"))
    kelly_fraction: float = float(os.getenv("KELLY_FRACTION", "0.25"))
    max_position_pct: float = float(os.getenv("MAX_POSITION_PCT", "0.05"))
    min_edge_bps: int = int(os.getenv("MIN_EDGE_BPS", "200"))

    data_dir: Path = Path(os.getenv("ZS_DATA_DIR", str(Path.home() / ".zero-strike")))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
