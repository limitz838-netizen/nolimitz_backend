import os
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine, case, or_
from sqlalchemy.orm import sessionmaker

from app.models import MT5VerificationJob, ClientMT5Account, MT5Worker
from app.security import decrypt_text
from mt5_service import verify_mt5_credentials_direct

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nolimitz.db")
WORKER_NAME = os.getenv("MT5_WORKER_NAME", "nolimitz-mt5-worker-1")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", 3))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 5))

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def utc_now():
    return datetime.now(timezone.utc)


def get_this_worker(db):
    return (
        db.query(MT5Worker)
        .filter(MT5Worker.worker_name == WORKER_NAME)
        .first()
    )


def should_retry(job: MT5VerificationJob) -> bool:
    retry_count = job.retry_count or 0
    max_retries = job.max_retries or MAX_RETRIES
    return retry_count < max_retries


def heartbeat_worker(db, worker: MT5Worker, error: str | None = None):
    worker.last_heartbeat = utc_now()
    worker.last_error = error
    db.commit()
    db.refresh(worker)


def process_one_job():
    db = SessionLocal()
    worker = None

    try:
        worker = get_this_worker(db)

        if not worker:
            print(f"[ERROR] Worker '{WORKER_NAME}' is not registered")
            return False

        if not worker.is_active:
            print(f"[ERROR] Worker '{WORKER_NAME}' is inactive")
            return False

        if worker.is_busy:
            return False

        heartbeat_worker(db, worker)

        job = (
            db.query(MT5VerificationJob)
            .filter(
                MT5VerificationJob.status.in_(["pending", "retry"]),
                or_(
                    MT5VerificationJob.worker_id.is_(None),
                    MT5VerificationJob.worker_id == worker.id,
                ),
            )
            .order_by(
                case(
                    (MT5VerificationJob.status == "retry", 1),
                    else_=0,
                ).desc(),
                MT5VerificationJob.id.desc(),
            )
            .first()
        )

        if not job:
            return False

        db.refresh(job)
        if job.status not in ["pending", "retry"]:
            return True

        newer_job_exists = (
            db.query(MT5VerificationJob)
            .filter(
                MT5VerificationJob.license_id == job.license_id,
                MT5VerificationJob.id > job.id,
                MT5VerificationJob.status.in_(["pending", "processing", "retry", "success"]),
            )
            .first()
        )

        if newer_job_exists:
            job.status = "cancelled"
            job.error_message = "Skipped due to newer verification request"
            job.finished_at = utc_now()
            db.commit()
            return True

        mt5_account = (
            db.query(ClientMT5Account)
            .filter(ClientMT5Account.id == job.client_mt5_account_id)
            .first()
        )

        if not mt5_account:
            job.status = "failed"
            job.error_message = "MT5 account record not found"
            job.finished_at = utc_now()
            db.commit()
            return True

        now = utc_now()

        # lock worker
        worker.is_busy = True
        worker.current_license_id = job.license_id
        worker.last_heartbeat = now
        worker.last_error = None

        # assign job to this worker
        job.worker_id = worker.id
        job.worker_name = WORKER_NAME
        job.status = "processing"
        job.started_at = now
        job.retry_count = (job.retry_count or 0) + 1

        db.commit()
        db.refresh(job)
        db.refresh(worker)

        try:
            real_password = decrypt_text(mt5_account.mt_password)

            print("WORKER VERIFY", {
                "license_id": job.license_id,
                "job_id": job.id,
                "worker": WORKER_NAME,
                "login": mt5_account.mt_login,
                "password_len": len(real_password or ""),
                "server": mt5_account.mt_server,
                "terminal_path": worker.terminal_path,
            })

            verified_data = verify_mt5_credentials_direct(
                mt_login=mt5_account.mt_login,
                mt_password=real_password,
                mt_server=mt5_account.mt_server,
                terminal_path=worker.terminal_path,
            )

            now = utc_now()

            mt5_account.account_name = verified_data.get("name")
            mt5_account.broker_name = verified_data.get("broker_name") or verified_data.get("server")
            mt5_account.balance = (
                str(verified_data.get("balance"))
                if verified_data.get("balance") is not None
                else None
            )
            mt5_account.equity = (
                str(verified_data.get("equity"))
                if verified_data.get("equity") is not None
                else None
            )
            mt5_account.last_verified_at = now
            mt5_account.verification_error = None
            mt5_account.is_verified = True
            mt5_account.is_active = True

            job.status = "success"
            job.error_message = None
            job.finished_at = now

            worker.last_heartbeat = now
            worker.last_error = None

            db.commit()
            print(
                f"[OK] worker={WORKER_NAME} "
                f"license_id={job.license_id} job_id={job.id} "
                f"(attempt {job.retry_count})"
            )

        except Exception as e:
            error_text = str(e).strip().replace("\n", " ").replace("\r", " ")[:500]
            if not error_text:
                error_text = "MT5 verification failed. Please check your login, password, and server."

            now = utc_now()

            mt5_account.account_name = None
            mt5_account.broker_name = None
            mt5_account.balance = None
            mt5_account.equity = None
            mt5_account.last_verified_at = now
            mt5_account.verification_error = error_text
            mt5_account.is_verified = False
            mt5_account.is_active = False

            if should_retry(job):
                job.status = "retry"
                job.error_message = f"Attempt {job.retry_count} failed: {error_text}"
                print(
                    f"[RETRY] worker={WORKER_NAME} "
                    f"license_id={job.license_id} job_id={job.id} "
                    f"attempt={job.retry_count}/{job.max_retries or MAX_RETRIES}"
                )
            else:
                job.status = "failed"
                job.error_message = f"Failed after {job.retry_count} attempts: {error_text}"
                job.finished_at = now
                print(
                    f"[FAILED] worker={WORKER_NAME} "
                    f"license_id={job.license_id} job_id={job.id} "
                    f"after {job.retry_count} attempts - {error_text}"
                )

            worker.last_heartbeat = now
            worker.last_error = error_text

            db.commit()

        return True

    finally:
        try:
            if worker:
                worker.is_busy = False
                worker.current_license_id = None
                worker.last_heartbeat = utc_now()
                db.commit()
        except Exception:
            pass

        db.close()


def main():
    print(f"Starting MT5 Verification Worker: {WORKER_NAME}")
    print(f"Max retries per job: {MAX_RETRIES} | Poll interval: {POLL_SECONDS}s")

    while True:
        found = process_one_job()
        if not found:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()