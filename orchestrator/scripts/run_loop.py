#!/usr/bin/env python
"""Run the reconciliation loop from a terminal. No notebook, no Dagster.

One pass is two questions put to the database, in this order:

  1. which jobs are we waiting on?  poll them, clear the finished, ask for
     checks. First, so freed capacity is usable in the same pass.
  2. which reaches are due?         check each: observe, gap, act.

Neither question needs anything remembered from the previous pass, which is
what makes this safe to interrupt. Every fact the loop relies on is in the
database before the call that wrote it returns, so Ctrl-C, a lost SSH session
or a reboot costs at most one duplicate job — and jobs are content addressed,
so a duplicate is wasted compute rather than a wrong answer.

Examples:

    python scripts/run_loop.py                  # until settled, then exit
    python scripts/run_loop.py --forever        # keep going, like a service
    python scripts/run_loop.py --once           # a single pass, for cron
    python scripts/run_loop.py --max-in-flight 4 --interval 15
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recon import check, db, jobs, processing, queue  # noqa: E402
from recon.config import settings  # noqa: E402
from recon.workers import LocalDockerRunner, job_env  # noqa: E402

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


def build_runner(args: argparse.Namespace) -> LocalDockerRunner:
    """A runner configured for this deployment.

    job_env() carries the S3 settings both clients inside the images need —
    boto3 reads AWS_ENDPOINT_URL, GDAL ignores it and wants its own. USE_CUDA
    is emptied unless --gpu is passed: the run image is CUDA-capable but does
    not require a GPU, and only an EMPTY string disables it (the job reads it
    as bool(os.environ.get(...)), so "false" and "0" are both truthy).
    """
    # Two halves, and both are needed. USE_CUDA tells the job to put `cuda` in
    # the solver's parameter file; --gpus lets the container see the device.
    # Either without the other fails: no flag means the CUDA solver starts with
    # no GPU, no USE_CUDA means the GPU sits idle while the CPU solver runs.
    extra = {} if args.gpu else {"USE_CUDA": ""}
    return LocalDockerRunner(
        network=settings.docker_network,
        env_vars=job_env(extra),
        platform=settings.docker_platform,
        gpus=(args.gpus or "all") if args.gpu else None,
        volumes=[f"{settings.docker_data_dir}:/data:ro"] if settings.docker_data_dir else [])


def one_pass(runner: LocalDockerRunner, max_in_flight: int) -> int:
    """Poll what is running, then check what is due. Returns jobs submitted."""
    for outcome in jobs.status_pass(runner):
        if outcome["status"] in ("succeeded", "failed"):
            logging.info("reach %s %s: %s after %ss - %s", outcome["reach_id"],
                         outcome["step"], outcome["status"], outcome["elapsed_s"],
                         outcome["action"])

    submitted = 0
    for row in queue.due_reaches():
        # EVERY due reach is checked, every pass. The cap limits submissions
        # only — never checking — because observation is how a finished job is
        # noticed at all. Skipping checks to hold the cap means a library can
        # sit complete in storage, unobserved, while the loop waits on an
        # unrelated job; the reach then looks unbuilt and gets resubmitted.
        at_cap = len(processing.in_flight()) >= max_in_flight
        result = check.run_check(row["reach_id"], runner, may_submit=not at_cap)
        if result.submitted_ref:
            submitted += 1
            logging.info("%s", result)
    return submitted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-in-flight", type=int, default=2,
                        help="jobs running at once (default 2; a normal-depth "
                             "scenario used ~3.4 cores, so match your machine)")
    parser.add_argument("--interval", type=float, default=20,
                        help="seconds between passes (default 20)")
    parser.add_argument("--once", action="store_true", help="a single pass, then exit")
    parser.add_argument("--forever", action="store_true",
                        help="keep going after the network settles")
    parser.add_argument("--gpu", action="store_true",
                        help="run the solver on a GPU: enables CUDA in the image "
                             "and gives containers device access (default: CPU)")
    parser.add_argument("--gpus", default=None, metavar="SPEC",
                        help="which devices, for docker --gpus (default 'all' with --gpu)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every check, not just the ones that act")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    # A pass checks every due reach, and most checks decide to do nothing —
    # so at default volume the loop reports what it DID: submissions, job
    # outcomes, and one summary line per pass. -v adds the per-check verdicts,
    # which is what you want when asking "why is this reach not moving?".
    logging.getLogger("recon").setLevel(logging.INFO if args.verbose else logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)

    runner = build_runner(args)
    logging.info("images: %s", " ".join(sorted(set(runner.images.values()))))
    logging.info("database %s | storage s3://%s",
                 settings.postgres_host, settings.artifacts_s3_bucket)

    started, passes, quiet = time.time(), 0, 0
    try:
        while True:
            passes += 1
            submitted = one_pass(runner, args.max_in_flight)
            now = db.one(TALLY)
            logging.info(
                "pass %-4d finished %s/%s  waiting %s  awaiting %s  in flight %s  "
                "resting %s  halted %s  submitted %s",
                passes, now["finished"], now["total"], now["waiting"], now["awaiting"],
                now["in_flight"], now["resting"], now["halted"], submitted)

            if args.once:
                break
            # Settled means nothing running and nothing started — not that every
            # reach is finished. A network with reaches awaiting inputs or halted
            # settles below 100%, and that is the correct place to stop.
            quiet = quiet + 1 if (now["in_flight"] == 0 and submitted == 0) else 0
            if quiet >= 2 and passes > 2 and not args.forever:
                logging.info("settled after %d passes in %.1f min",
                             passes, (time.time() - started) / 60)
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info("stopped by hand; nothing lost — in-flight jobs are "
                     "recorded in the database and will be picked up again")

    final = db.one(TALLY)
    if final["halted"]:
        logging.warning("%s reach(es) halted and need a person: "
                        "see reach_status, then processing.clear_halt(reach_id)",
                        final["halted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
