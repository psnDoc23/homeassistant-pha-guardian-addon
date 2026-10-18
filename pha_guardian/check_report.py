"""Summarize one round of checks so "healthy" is only claimed when it is earned."""

from datetime import datetime, timezone


STATUS_OK = "ok"
STATUS_ATTENTION = "attention"
STATUS_INCOMPLETE = "incomplete"


def is_failed_check(issue: dict) -> bool:
    """Checkers report their own failure as an 'unknown' issue ending in check_failed."""
    return (
        issue.get("severity") == "unknown"
        or str(issue.get("id", "")).endswith("check_failed")
    )


def build_report(issues: list, checked_at: datetime | None = None) -> dict:
    """
    status:
      ok          every check finished and found nothing
      attention   at least one real finding (some checks may also have failed)
      incomplete  nothing found, but at least one check could not finish
    """
    checked_at = checked_at or datetime.now(timezone.utc)
    findings = [i for i in issues if not is_failed_check(i)]
    failed = [i for i in issues if is_failed_check(i)]

    if findings:
        status = STATUS_ATTENTION
    elif failed:
        status = STATUS_INCOMPLETE
    else:
        status = STATUS_OK

    return {
        "status": status,
        "checked_at": checked_at.replace(microsecond=0).isoformat(),
        "finding_count": len(findings),
        "all_checks_completed": not failed,
        "failed_checks": [
            {
                "id": i.get("id", ""),
                "title": i.get("title", "A check could not finish"),
                "detail": (str(i.get("detail") or "").splitlines() or [""])[0],
            }
            for i in failed
        ],
    }


class CheckHistory:
    """Remembers when Guardian last tried, and last finished every check."""

    def __init__(self):
        self.last_attempt_at = None
        self.last_complete_at = None
        self.last_report = None

    def record(self, report: dict) -> None:
        self.last_attempt_at = report["checked_at"]
        if report["all_checks_completed"]:
            self.last_complete_at = report["checked_at"]
        self.last_report = report

    def snapshot(self) -> dict:
        return {
            "last_attempt_at": self.last_attempt_at,
            "last_complete_at": self.last_complete_at,
        }
