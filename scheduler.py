"""Job scheduler using APScheduler."""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import get_config
from sync import trigger_sync
from device_monitor import run_device_monitor_job
from bot_manager import run_bot_manager_job

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: BackgroundScheduler | None = None


class SchedulerManager:
    """Manages scheduled jobs."""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self._jobs_registered = False

    def _register_sync_jobs(self):
        """Register sync jobs based on config."""
        config = get_config()
        sync_times = config.sync_times

        logger.info(f"Registering sync jobs for times: {sync_times}")

        for time_str in sync_times:
            try:
                hour, minute = map(int, time_str.split(":"))

                # Create cron trigger: every day at specified time
                trigger = CronTrigger(hour=hour, minute=minute)

                job = self.scheduler.add_job(
                    trigger_sync,
                    trigger=trigger,
                    id=f"sync_{hour}_{minute}",
                    name=f"Sync at {time_str}",
                    replace_existing=True,
                )

                logger.info(f"Registered sync job at {time_str}")

            except Exception as e:
                logger.error(f"Failed to register sync job for {time_str}: {e}")

    def _register_device_monitor_job(self):
        """Register device monitor job (every N hours)."""
        config = get_config()
        interval_hours = config.device_check_interval_hours

        logger.info(f"Registering device monitor job (every {interval_hours} hours)")

        trigger = IntervalTrigger(hours=interval_hours)

        job = self.scheduler.add_job(
            run_device_monitor_job,
            trigger=trigger,
            id="device_monitor",
            name=f"Device monitor (every {interval_hours}h)",
            replace_existing=True,
        )

        logger.info("Device monitor job registered")

    def _register_bot_manager_job(self):
        """Register bot manager job (every N minutes)."""
        config = get_config()
        interval_minutes = config.bot_check_interval_minutes

        logger.info(f"Registering bot manager job (every {interval_minutes} minutes)")

        trigger = IntervalTrigger(minutes=interval_minutes)

        job = self.scheduler.add_job(
            run_bot_manager_job,
            trigger=trigger,
            id="bot_manager",
            name=f"Bot manager (every {interval_minutes}m)",
            replace_existing=True,
        )

        logger.info("Bot manager job registered")

    def register_jobs(self):
        """Register all scheduled jobs."""
        if self._jobs_registered:
            logger.debug("Jobs already registered")
            return

        self._register_sync_jobs()
        self._register_device_monitor_job()
        self._register_bot_manager_job()

        self._jobs_registered = True
        logger.info("All jobs registered")

    def start(self):
        """Start the scheduler."""
        if self.scheduler.running:
            logger.info("Scheduler is already running")
            return

        try:
            self.register_jobs()
            self.scheduler.start()
            logger.info("Scheduler started")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
            raise

    def stop(self):
        """Stop the scheduler."""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=True)
                logger.info("Scheduler stopped")
        except Exception as e:
            logger.error(f"Failed to stop scheduler: {e}")

    def trigger_sync_now(self):
        """Trigger sync immediately."""
        logger.info("Triggering sync immediately...")
        result = trigger_sync()
        return result

    def get_jobs(self) -> list[dict]:
        """Get list of scheduled jobs."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "trigger": str(job.trigger),
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                }
            )
        return jobs


def get_scheduler() -> SchedulerManager:
    """Get singleton scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerManager()
    return _scheduler


def stop_scheduler():
    """Stop the global scheduler."""
    if _scheduler:
        _scheduler.stop()
