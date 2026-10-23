"""Configuration loading: versioned YAML for behaviour, environment for secrets.

Rules
- Behaviour (storage, sources, executors, limits) lives in config/*.yaml under git.
- Secrets live only in environment variables (or a git-ignored .env file).
- Secret values are wrapped in SecretStr so they never appear in repr/logs.
- Unknown keys are rejected, so a typo cannot silently disable a safeguard.
- Budget zero is enforced by validation, not by convention (ADR-0006).
- Two folders, never mixed (ADR-0007):
    ORPHEUS_HOME       the data folder (database, PDF archive, inbox) — may be on an external disk;
    ORPHEUS_LOCAL_DIR  this PC's own folder (what it has seen, its backups) — internal disk only.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------- secrets (environment only) ----------

class Secrets(_Strict):
    contact_email: str | None = None
    ncbi_api_key: SecretStr | None = None
    semantic_scholar_api_key: SecretStr | None = None
    openalex_api_key: SecretStr | None = None

    def present(self) -> dict[str, bool]:
        """Which secrets are configured — names only, never values."""
        return {name: getattr(self, name) is not None for name in type(self).model_fields}


_SECRET_ENV = {
    "contact_email": "ORPHEUS_CONTACT_EMAIL",
    "ncbi_api_key": "NCBI_API_KEY",
    "semantic_scholar_api_key": "SEMANTIC_SCHOLAR_API_KEY",
    "openalex_api_key": "OPENALEX_API_KEY",
}


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    # utf-8-sig: Notepad can save "UTF-8 with BOM"; the BOM must not become part of the first key.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if val:
            values[key.strip()] = val
    return values


def environment(env: dict[str, str] | None = None, dotenv: Path | None = None) -> dict[str, str]:
    """The .env file of this PC, overridden by real environment variables (or by `env`, in tests)."""
    merged = _read_dotenv(dotenv or ROOT / ".env")
    merged.update({k: v for k, v in (os.environ if env is None else env).items() if v})
    return merged


def load_secrets(env: dict[str, str] | None = None, dotenv: Path | None = None) -> Secrets:
    merged = environment(env, dotenv)
    return Secrets(**{field: merged[var] for field, var in _SECRET_ENV.items() if var in merged})


# ---------- versioned behaviour config ----------

class Limits(_Strict):
    paid_services_allowed: bool
    monthly_budget_usd: float = Field(ge=0)
    run_timeout_seconds: int = Field(gt=0)
    max_retries: int = Field(ge=0, le=10)
    max_requests_per_second: float = Field(gt=0)

    @model_validator(mode="after")
    def _budget_zero_means_zero(self):
        if not self.paid_services_allowed and self.monthly_budget_usd != 0:
            raise ValueError("paid services are not allowed, so monthly_budget_usd must be 0")
        return self


class Storage(_Strict):
    """Locations inside the data folder. Always relative to ORPHEUS_HOME, so the folder can move
    between PCs and drive letters without breaking anything stored in the database."""
    db_path: str
    archive_dir: str
    inbox_dir: str
    journals_csv: str

    @field_validator("db_path", "archive_dir", "inbox_dir", "journals_csv")
    @classmethod
    def _relative_inside_home(cls, v: str) -> str:
        p = PurePosixPath(v.replace("\\", "/"))
        if not p.parts or p.is_absolute() or ".." in p.parts or ":" in v:
            raise ValueError(f"{v!r}: storage paths must be relative to ORPHEUS_HOME and must not contain '..'")
        return v


class LocalSettings(_Strict):
    """Behaviour of this PC's local folder (ORPHEUS_LOCAL_DIR)."""
    auto_backup_minutes: int = Field(ge=0)     # 0 disables automatic backups
    keep_local_backups: int = Field(ge=1)


class ProjectConfig(_Strict):
    name: str
    version: str
    responsible_human: str | None
    language: str
    research_domains: list[str]
    storage: Storage
    local: LocalSettings
    limits: Limits
    require_human_approval_for: list[str]


class SourceConfig(_Strict):
    id: str
    name: str
    kind: Literal["metadata", "fulltext", "bibliometric", "editorial", "registry", "local"]
    acquisition: Literal["api", "manual", "local_files"]
    enabled: bool
    base_url: str | None = None
    auth: Literal["none", "api_key", "institutional", "licence"]
    cost: Literal["free", "paid"]
    authoritative_for: list[str] = []
    notes: str = ""
    verified_on: date | None = None

    @model_validator(mode="after")
    def _licensed_sources_are_manual(self):
        # Licensed/institutional services are never scraped or called automatically:
        # the user exports what their licence allows and ORPHEUS imports the file.
        if self.auth in ("licence", "institutional") and self.acquisition != "manual":
            raise ValueError(f"{self.id}: licensed or institutional sources must use acquisition 'manual'")
        return self


class ExecutorConfig(_Strict):
    id: Literal["claude_session", "local_llm", "chat_manuale"]
    enabled: bool
    cost: Literal["free", "paid"]
    description: str


class RouteConfig(_Strict):
    task_class: str
    executor: Literal["deterministic", "claude_session", "local_llm", "chat_manuale"]
    independent_validation: bool = False
    rationale: str


class ModelsConfig(_Strict):
    executors: list[ExecutorConfig]
    routes: list[RouteConfig]

    @model_validator(mode="after")
    def _routes_use_enabled_executors(self):
        enabled = {e.id for e in self.executors if e.enabled}
        for r in self.routes:
            if r.executor != "deterministic" and r.executor not in enabled:
                raise ValueError(f"route {r.task_class!r} uses executor {r.executor!r}, which is not enabled")
        return self


def _load_yaml(name: str, config_dir: Path | None = None) -> dict:
    path = (config_dir or CONFIG_DIR) / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_project(config_dir: Path | None = None) -> ProjectConfig:
    return ProjectConfig(**_load_yaml("orpheus.yaml", config_dir)["project"])


def load_sources(config_dir: Path | None = None) -> list[SourceConfig]:
    return [SourceConfig(**s) for s in _load_yaml("sources.yaml", config_dir)["sources"]]


def load_models(config_dir: Path | None = None) -> ModelsConfig:
    return ModelsConfig(**_load_yaml("models.yaml", config_dir))


def check_budget_policy(project: ProjectConfig, sources: list[SourceConfig], models: ModelsConfig) -> list[str]:
    """Return violations of the budget policy (empty list = compliant)."""
    if project.limits.paid_services_allowed:
        return []
    problems = [f"source {s.id} is paid but enabled" for s in sources if s.enabled and s.cost == "paid"]
    problems += [f"executor {e.id} is paid but enabled" for e in models.executors if e.enabled and e.cost == "paid"]
    return problems


def home(env: dict[str, str] | None = None, dotenv: Path | None = None) -> Path:
    """The data folder (ORPHEUS_HOME); defaults to the repository root when not configured."""
    value = environment(env, dotenv).get("ORPHEUS_HOME")
    return Path(value).expanduser().absolute() if value else ROOT


def home_is_configured(env: dict[str, str] | None = None, dotenv: Path | None = None) -> bool:
    return bool(environment(env, dotenv).get("ORPHEUS_HOME"))


def default_local_dir(env: dict[str, str]) -> Path:
    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ORPHEUS"
    return Path(env.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "orpheus"


def local_dir(env: dict[str, str] | None = None, dotenv: Path | None = None) -> Path:
    """This PC's own folder (ORPHEUS_LOCAL_DIR): seen-state register and local backups."""
    merged = environment(env, dotenv)
    value = merged.get("ORPHEUS_LOCAL_DIR")
    return Path(value).expanduser().absolute() if value else default_local_dir(merged)


def data_path(rel: str, env: dict[str, str] | None = None, dotenv: Path | None = None) -> Path:
    return home(env, dotenv) / rel


_CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "il mio drive", "my drive", "icloud", "box sync")


def path_warnings(home_dir: Path, local: Path, *, home_configured: bool) -> list[str]:
    """Problems in where the two folders are placed (messages for the user, in Italian)."""
    warnings = []
    if not home_configured:
        warnings.append("ORPHEUS_HOME non è impostata nel file .env: i dati finiscono nella cartella del codice.")
    for name, folder in (("ORPHEUS_HOME", home_dir), ("ORPHEUS_LOCAL_DIR", local)):
        if any(m in str(folder).lower() for m in _CLOUD_MARKERS):
            warnings.append(f"{name} ({folder}) sembra dentro una cartella sincronizzata nel cloud: "
                            "la sincronizzazione può danneggiare il database. Spostala fuori.")
    try:
        local.resolve().relative_to(home_dir.resolve())
        warnings.append(f"ORPHEUS_LOCAL_DIR ({local}) è dentro ORPHEUS_HOME: deve stare sul disco interno del PC, "
                        "altrimenti viaggia con i dati e le protezioni contro le copie vecchie non funzionano.")
    except ValueError:
        pass
    return warnings
