#!/usr/bin/env python
"""Run the reconciliation loop from a terminal. No notebook, no Dagster.

One pass is two questions put to the database, in this order:

  1. which jobs are we waiting on?  poll them, clear the finished, ask for
     checks. First, so a finished job is noticed in the same pass.
  2. which reaches are due?         check each: observe, gap, act.

Neither question needs anything remembered from the previous pass, which is
what makes this safe to interrupt. Every fact the loop relies on is in the
database before the call that wrote it returns, so Ctrl-C, a lost SSH session
or a reboot costs at most one duplicate job — and jobs are content addressed,
so a duplicate is wasted compute rather than a wrong answer.

There is no concurrency limit here. SEPEX is the execution layer: it keeps the
queue and admits work against its own resource pool, so this loop submits
whatever is needed and lets SEPEX decide when it runs. A limit here could only
withhold work SEPEX had room for.

Examples:

    python scripts/reconcile.py                  # until settled, then exit
    python scripts/reconcile.py --forever        # keep going, like a service
    python scripts/reconcile.py --once           # a single pass, for cron
    python scripts/reconcile.py --gpu --interval 15
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recon import check, db, jobs, queue
from recon.config import settings
from recon.execution import ExecutionService, SepexClient

TALLY = """
    SELECT count(*) FILTER (WHERE state = 'finished')           AS finished,
           count(*) FILTER (WHERE state = 'waiting_downstream') AS waiting,
           count(*) FILTER (WHERE state = 'awaiting_inputs')    AS awaiting,
           count(*) FILTER (WHERE state = 'in_flight')          AS in_flight,
           count(*) FILTER (WHERE state = 'resting')            AS resting,
           count(*) FILTER (WHERE state = 'halted')             AS halted,
           count(*)                                             AS total
    FROM reach_status
"""


def build_execution(args: argparse.Namespace) -> ExecutionService:
    """The execution layer. There is one, and it is SEPEX.

    --gpu is not an execution setting: it selects which SEPEX PROCESS is asked for,
    because a CPU and a GPU run are separate registered processes. Everything
    else about how a job runs lives in that process's plugin.
    """
    logging.info("SEPEX at %s", settings.sepex_url)
    return SepexClient(base_url=settings.sepex_url)


def one_pass(execution: ExecutionService, gpu: bool) -> int:
    """Poll what is running, then check what is due. Returns jobs submitted."""
    for outcome in jobs.poll_in_flight(execution):
        if outcome["status"] in ("succeeded", "failed"):
            logging.info(
                "reach %s %s: %s after %ss - %s",
                outcome["reach_id"],
                outcome["step"],
                outcome["status"],
                outcome["elapsed_s"],
                outcome["action"],
            )

    submitted = 0
    for row in queue.due_reaches():
        # Every due reach is checked and, if it has a gap, submitted. Holding
        # work back here would be guessing at SEPEX's capacity from outside;
        # SEPEX queues what it cannot start yet, and a queued job is the system
        # working rather than a problem to avoid.
        result = check.run_check(row["reach_id"], execution, gpu=gpu)
        if result.submitted_ref:
            submitted += 1
            logging.info("%s", result)
    return submitted


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--interval", type=float, default=20, help="seconds between passes (default 20)"
    )
    parser.add_argument("--once", action="store_true", help="a single pass, then exit")
    parser.add_argument(
        "--forever", action="store_true", help="keep going after the network settles"
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="ask for the GPU build of the run job (default: CPU). "
        "How many run at once is SEPEX's to decide, not this loop's",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log every check, not just the ones that act",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # A pass checks every due reach, and most checks decide to do nothing —
    # so at default volume the loop reports what it DID: submissions, job
    # outcomes, and one summary line per pass. -v adds the per-check verdicts,
    # which is what you want when asking "why is this reach not moving?".
    logging.getLogger("recon").setLevel(
        logging.INFO if args.verbose else logging.WARNING
    )
    logging.getLogger("botocore").setLevel(logging.WARNING)

    execution = build_execution(args)
    logging.info(
        "database %s | storage s3://%s | run job %s",
        settings.postgres_host,
        settings.artifacts_s3_bucket,
        check.RUN_ND_PROCESSES.get(("lisflood", args.gpu), "?"),
    )

    started, passes, quiet = time.time(), 0, 0
    try:
        while True:
            passes += 1
            submitted = one_pass(execution, args.gpu)
            now = db.one(TALLY)
            logging.info(
                "pass %-4d finished %s/%s  waiting %s  awaiting %s  in flight %s  "
                "resting %s  halted %s  submitted %s",
                passes,
                now["finished"],
                now["total"],
                now["waiting"],
                now["awaiting"],
                now["in_flight"],
                now["resting"],
                now["halted"],
                submitted,
            )

            if args.once:
                break
            # Settled means nothing running and nothing started — not that every
            # reach is finished. A network with reaches awaiting inputs or halted
            # settles below 100%, and that is the correct place to stop.
            quiet = quiet + 1 if (now["in_flight"] == 0 and submitted == 0) else 0
            if quiet >= 2 and passes > 2 and not args.forever:
                logging.info(
                    "settled after %d passes in %.1f min",
                    passes,
                    (time.time() - started) / 60,
                )
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info(
            "stopped by hand; nothing lost — in-flight jobs are "
            "recorded in the database and will be picked up again"
        )

    final = db.one(TALLY)
    if final["halted"]:
        logging.warning(
            "%s reach(es) halted and need a person: "
            "see reach_status, then processing.clear_halt(reach_id)",
            final["halted"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
