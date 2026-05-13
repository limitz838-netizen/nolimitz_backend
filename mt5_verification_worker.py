import os
import time
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

from sqlalchemy import create_engine, case, or_
from sqlalchemy.orm import sessionmaker

from app.models import MT5VerificationJob, ClientMT5Account, MT5Worker
from app.security import decrypt_text
from mt5_service import verify_mt5_credentials_direct

# ========================= CONFIG =========================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./nolimitz.db")
WORKER_NAME = os.getenv("MT5_WORKER_NAME", "nolimitz-mt5-worker-1")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", 5))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 5))

# ========================= LOGGING =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ========================= DATABASE =========================
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def utc_now():
    return datetime.now(timezone.utc)


# ========================= HELPERS =========================
def get_this_worker(db):
    return db.query(MT5Worker).filter(MT5Worker.worker_name == WORKER_NAME).first()


def should_retry(job: MT5VerificationJob) -> bool:
    return (job.retry_count or 0) < (job.max_retries or MAX_RETRIES)


def heartbeat_worker(db, worker: MT5Worker, error: Optional[str] = None):
    worker.last_heartbeat = utc_now()
    worker.last_error = error
    db.commit()


def process_one_job() -> bool:
    db = SessionLocal()
    worker = None

    try:
        worker = get_this_worker(db)
        if not worker:
            logger.error(f"Worker '{WORKER_NAME}' not registered in database")
            return False

        if not worker.is_active:
            logger.warning(f"Worker '{WORKER_NAME}' is inactive")
            return False

        if worker.is_busy:
            return False

        heartbeat_worker(db, worker)

        # Reset dead workers
        stuck_worker_time = utc_now() - timedelta(minutes=5)

        db.query(MT5Worker).filter(
            MT5Worker.is_busy == True,
            MT5Worker.last_heartbeat < stuck_worker_time,
        ).update({
            "is_busy": False,
            "current_license_id": None,
        })

        db.commit()

        # Reset stuck processing jobs
        stuck_time = utc_now() - timedelta(minutes=10)

        db.query(MT5VerificationJob).filter(
            MT5VerificationJob.status == "processing",
            MT5VerificationJob.started_at < stuck_time,
        ).update({
            "status": "retry",
            "worker_id": None,
            "worker_name": None,
        })

        db.commit()

        # Get next job (retry first, then pending)
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
                case((MT5VerificationJob.status == "retry", 1), else_=0).desc(),
                MT5VerificationJob.id.desc(),
            )
            .first()
        )

        if not job:
            return False

        # Skip if newer job exists for same license
        newer_job = db.query(MT5VerificationJob).filter(
            MT5VerificationJob.license_id == job.license_id,
            MT5VerificationJob.id > job.id,
            MT5VerificationJob.status.in_(["pending", "processing", "retry", "success"]),
        ).first()

        if newer_job:
            job.status = "cancelled"
            job.error_message = "Cancelled - newer verification request exists"
            job.finished_at = utc_now()
            db.commit()
            return True

        mt5_account = db.query(ClientMT5Account).filter(
            ClientMT5Account.id == job.client_mt5_account_id
        ).first()

        if not mt5_account:
            job.status = "failed"
            job.error_message = "ClientMT5Account record not found"
            job.finished_at = utc_now()
            db.commit()
            return True

        # ================== LOCK & PROCESS ==================
        worker.is_busy = True
        worker.current_license_id = job.license_id
        job.worker_id = worker.id
        job.worker_name = WORKER_NAME
        job.status = "processing"
        job.started_at = utc_now()
        job.retry_count = (job.retry_count or 0) + 1

        db.commit()
        db.refresh(job)
        db.refresh(worker)

        success = False
        error_text = None

        try:
            real_password = decrypt_text(mt5_account.mt_password)

            logger.info(f"Verifying MT5 | Job={job.id} | Login={mt5_account.mt_login} | Server={mt5_account.mt_server}")

            if not os.path.exists(worker.terminal_path):
                raise Exception(
                    f"MT5 terminal not found: {worker.terminal_path}"
            )

            verified_data = verify_mt5_credentials_direct(
                mt_login=mt5_account.mt_login,
                mt_password=real_password,
                mt_server=mt5_account.mt_server,
                terminal_path=worker.terminal_path,
            )

            # Update success
            mt5_account.account_name = verified_data.get("name")
            mt5_account.broker_name = verified_data.get("broker_name") or verified_data.get("server")
            mt5_account.balance = str(verified_data.get("balance")) if verified_data.get("balance") is not None else None
            mt5_account.equity = str(verified_data.get("equity")) if verified_data.get("equity") is not None else None
            mt5_account.last_verified_at = utc_now()
            mt5_account.verification_error = None
            mt5_account.is_verified = True
            mt5_account.is_active = True

            job.status = "success"
            job.error_message = None
            job.finished_at = utc_now()

            logger.info(f"✅ Verification SUCCESS | Job={job.id} | Login={mt5_account.mt_login}")
            success = True

        except Exception as e:
            error_text = str(e).strip()[:500] or "Unknown verification error"
            logger.error(f"❌ Verification failed | Job={job.id} | Error: {error_text}")

            mt5_account.verification_error = error_text
            mt5_account.is_verified = False
            mt5_account.is_active = False
            mt5_account.last_verified_at = utc_now()

            if should_retry(job):
                job.status = "retry"
                job.error_message = f"Attempt {job.retry_count} failed: {error_text}"
            else:
                job.status = "failed"
                job.error_message = f"Failed after {job.retry_count} attempts: {error_text}"
                job.finished_at = utc_now()

            time.sleep(1) 

        finally:
            heartbeat_worker(db, worker, error_text)
            db.commit()

        return True

    except Exception as e:
        logger.error(f"Critical error in process_one_job: {e}", exc_info=True)
        return False

    finally:
        # Always unlock worker
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
    logger.info(f"🚀 MT5 Verification Worker Started → {WORKER_NAME}")
    logger.info(f"Poll interval: {POLL_SECONDS}s | Max retries: {MAX_RETRIES}")

    while True:
        try:
            found_job = process_one_job()
            if not found_job:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Worker stopped by user")
            break
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            time.sleep(POLL_SECONDS * 2)


if __name__ == "__main__":
    main()