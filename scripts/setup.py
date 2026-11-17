#!/usr/bin/env python3
"""Generate local credentials without printing them or overwriting existing ones."""
from pathlib import Path
import os
import secrets

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / ".env"
if path.exists():
    print(".env가 이미 있습니다. 기존 설정을 보존합니다.")
else:
    values = {
        "SORT_HOST": "0.0.0.0", "SORT_PORT": "8100", "SORT_DATA_DIR": str(ROOT / "data"),
        "SORT_ADMIN_PASSWORD": secrets.token_urlsafe(24), "SORT_ENROLLMENT_CODE": secrets.token_urlsafe(16),
        "SORT_COOKIE_SECURE": "false", "SORT_JUDGE_IMAGE": "sorting-judge:local", "SORT_CPU": "2",
        "SORT_REPETITIONS": "10", "SORT_TIME_LIMIT": "2.0", "SORT_WALL_LIMIT": "4.0", "SORT_MEMORY_MB": "128",
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.writelines(f"{key}={value}\n" for key, value in values.items())
    print(".env를 생성했습니다. 관리자 ID는 admin입니다.")
    print("관리자 비밀번호와 수업 참여 코드는 .env에서 확인하세요. (권한 600)")
