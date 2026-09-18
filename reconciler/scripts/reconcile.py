#!/usr/bin/env python
"""Run the reconciliation loop from a terminal.

Each pass checks the reaches that need looking at and acts on what it finds.
By default the loop exits once the network settles.

Usage:
    just reconcile

    python scripts/reconcile.py [--interval SECONDS] [--once] [--forever] [-v]

Options:
    --interval   seconds between passes (default 20)
    --once       a single pass, then exit
    --forever    keep going after the network settles
    -v           log every check, not just the ones that act
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
           count(*) FILTER (WHERE state = 'awaiting_downstream') AS waiting,
           count(*) FILTER (WHERE state = 'awaiting_inputs')    AS awaiting,
           count(*) FILTER (WHERE state = 'in_flight')          AS in_flight,
           count(*) FILTER (WHERE state = 'resting')            AS resting,
           count(*) FILTER (WHERE state = 'halted')             AS halted,
           count(*)                                             AS total
    FROM reach_status
"""


def build_execution(args: argparse.Namespace) -> ExecutionService:
    """The execution layer."""
    logging.info("SEPEX at %s", settings.sepex_url)
    return SepexClient(base_url=settings.sepex_url)


def one_pass(execution: ExecutionService) -> int:
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
        result = check.run_check(row["reach_id"], execution)
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
    logging.getLogger("recon").setLevel(
        logging.INFO if args.verbose else logging.WARNING
    )
    logging.getLogger("botocore").setLevel(logging.WARNING)

    execution = build_execution(args)
    logging.info(
        "database %s | storage %s | run job %s",
        settings.postgres_host,
        settings.twod_fim_data_root_prefix,
        check.RUN_ND_PROCESSES.get(("lisflood", check.gpu_available()), "?"),
    )

    started, passes, quiet = time.time(), 0, 0
    try:
        while True:
            passes += 1
            submitted = one_pass(execution)
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
