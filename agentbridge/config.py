from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class AgentConfig:
    name: str
    provider: str
    model: str
    role: str


@dataclass
class Settings:
    workspace_dir: Path
    mailboard_file: Path
    max_ledger_context: int
    max_turns: int
    require_client_keys: bool


@dataclass
class Config:
    agents: list[AgentConfig]
    settings: Settings

    def get_agent(self, name: str) -> AgentConfig:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise KeyError(
            f"No agent named '{name}' in config.yaml. "
            f"Configured agents: {', '.join(a.name for a in self.agents)}"
        )


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.yaml to config.yaml and edit it."
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    agents = [AgentConfig(**a) for a in raw.get("agents", [])]
    if not agents:
        raise ValueError(f"{path} must define at least one agent under 'agents:'")

    settings_raw = raw.get("settings", {})
    settings = Settings(
        workspace_dir=Path(settings_raw.get("workspace_dir", "workspace")),
        mailboard_file=Path(settings_raw.get("mailboard_file", "mailboard.json")),
        max_ledger_context=int(settings_raw.get("max_ledger_context", 6)),
        max_turns=int(settings_raw.get("max_turns", 20)),
        require_client_keys=bool(settings_raw.get("require_client_keys", False)),
    )
    return Config(agents=agents, settings=settings)
