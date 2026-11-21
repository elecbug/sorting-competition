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
    admin_password: str = ""
    cookie_secure: bool = False
    image: str = "sorting-judge:local"
    cpu: int = 2
    repetitions: int = 10
    time_limit: float = 2.0
    wall_limit: float = 4.0
    memory_mb: int = 128
    job_dir: Path | None = None
    job_volume: str = ""
    warmup_runs: int = 3
    trim_fraction: float = 0.1
    settle_seconds: float = 0.2
    access_code: str = ""
    benchmark_cpus: str = "auto"
    prepared_dir: Path | None = None
    prepared_volume: str = ""
    prestart_check: bool = True

    @classmethod
    def from_env(cls):
        load_env()
        settings = cls(
            data_dir=Path(os.getenv("SORT_DATA_DIR", str(ROOT / "data"))).resolve(),
            admin_password=os.getenv("SORT_ADMIN_PASSWORD", ""),
            cookie_secure=os.getenv("SORT_COOKIE_SECURE", "false").lower() == "true",
            image=os.getenv("SORT_JUDGE_IMAGE", "sorting-judge:local"),
            cpu=int(os.getenv("SORT_CPU", "2")),
            repetitions=int(os.getenv("SORT_REPETITIONS", "10")),
            time_limit=float(os.getenv("SORT_TIME_LIMIT", "2")),
            wall_limit=float(os.getenv("SORT_WALL_LIMIT", "4")),
            memory_mb=int(os.getenv("SORT_MEMORY_MB", "128")),
            job_dir=Path(os.environ["SORT_JOB_DIR"]).resolve() if os.getenv("SORT_JOB_DIR") else None,
            job_volume=os.getenv("SORT_JOB_VOLUME", ""),
            warmup_runs=int(os.getenv("SORT_WARMUP_RUNS", "3")),
            trim_fraction=float(os.getenv("SORT_TRIM_FRACTION", "0.1")),
            settle_seconds=float(os.getenv("SORT_SETTLE_SECONDS", "0.2")),
            access_code=os.getenv("SORT_ACCESS_CODE", ""),
            prepared_dir=Path(os.environ["SORT_PREPARED_DIR"]) if os.getenv("SORT_PREPARED_DIR") else None,
            prepared_volume=os.getenv("SORT_PREPARED_VOLUME", ""),
            prestart_check=os.getenv("SORT_PRESTART_CHECK", "true").lower() == "true",
            benchmark_cpus=os.getenv("SORT_BENCHMARK_CPUS", "auto"),
        )
        if not 3 <= settings.repetitions <= 100:
            raise RuntimeError("Repetitions must be between 3 and 100.")
        if not (0 < settings.time_limit <= 10 and settings.time_limit < settings.wall_limit <= 20 and 32 <= settings.memory_mb <= 256):
            raise RuntimeError("Time or memory limits are outside the supported range.")
        if not (1 <= settings.warmup_runs <= 10 and 0 <= settings.trim_fraction <= 0.25 and 0 <= settings.settle_seconds <= 3):
            raise RuntimeError("Invalid warm-up, trimming, or settling configuration.")
        return settings
