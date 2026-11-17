from dataclasses import dataclass
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    admin_password: str
    enrollment_code: str
    cookie_secure: bool = False
    image: str = "sorting-judge:local"
    cpu: int = 2
    repetitions: int = 10
    time_limit: float = 2.0
    wall_limit: float = 4.0
    memory_mb: int = 128

    @classmethod
    def from_env(cls):
        load_env()
        settings = cls(
            data_dir=Path(os.getenv("SORT_DATA_DIR", str(ROOT / "data"))).resolve(),
            admin_password=os.getenv("SORT_ADMIN_PASSWORD", ""),
            enrollment_code=os.getenv("SORT_ENROLLMENT_CODE", ""),
            cookie_secure=os.getenv("SORT_COOKIE_SECURE", "false").lower() == "true",
            image=os.getenv("SORT_JUDGE_IMAGE", "sorting-judge:local"),
            cpu=int(os.getenv("SORT_CPU", "2")),
            repetitions=int(os.getenv("SORT_REPETITIONS", "10")),
            time_limit=float(os.getenv("SORT_TIME_LIMIT", "2")),
            wall_limit=float(os.getenv("SORT_WALL_LIMIT", "4")),
            memory_mb=int(os.getenv("SORT_MEMORY_MB", "128")),
        )
        if not settings.admin_password or not settings.enrollment_code:
            raise RuntimeError("Run .venv/bin/python scripts/setup.py or set both credentials in .env.")
        if not 3 <= settings.repetitions <= 30:
            raise RuntimeError("Repetitions must be between 3 and 30.")
        if not (0 < settings.time_limit <= 10 and settings.time_limit < settings.wall_limit <= 20 and 32 <= settings.memory_mb <= 256):
            raise RuntimeError("Time or memory limits are outside the supported range.")
        return settings
