import argparse
import os
import signal
import subprocess
import sys
import time

from dataclasses import (
    dataclass
)

from pathlib import (
    Path
)

from backend.runtime.process_lock import (
    SingleInstanceLock
)


REPO_ROOT = (

    Path(
        __file__
    )
    .resolve()
    .parents[2]
)


CHECK_INTERVAL_SECONDS = 1

MAX_RESTART_DELAY_SECONDS = 30

STABLE_RUNTIME_SECONDS = 60

SHUTDOWN_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class WorkerSpec:

    name: str

    module: str


@dataclass
class WorkerState:

    spec: WorkerSpec

    process: (
        subprocess.Popen
        | None
    ) = None

    started_at: (
        float
        | None
    ) = None

    restart_count: int = 0

    next_start_at: float = 0.0


WORKERS = (

    WorkerSpec(

        name=
            "tick_ingestor",

        module=
            "backend.data_engine."
            "run_tick_ingestor"
    ),

    WorkerSpec(

        name=
            "m5_bar_ingestor",

        module=
            "backend.candle_engine."
            "run_mt5_bar_ingestor"
    )
)


class MarketDataSupervisor:

    def __init__(
        self
    ):

        self.states = [

            WorkerState(
                spec=spec
            )

            for spec
            in WORKERS
        ]


        self.stop_requested = False


    @staticmethod
    def _creation_options():

        if (
            os.name
            == "nt"
        ):

            return {

                "creationflags":
                    subprocess
                    .CREATE_NEW_PROCESS_GROUP
            }


        return {

            "start_new_session":
                True
        }


    def _start_worker(
        self,
        state
    ):

        environment = dict(
            os.environ
        )


        environment[
            "PYTHONUNBUFFERED"
        ] = "1"


        command = [

            sys.executable,

            "-m",

            state.spec.module
        ]


        process = (
            subprocess.Popen(

                command,

                cwd=
                    str(
                        REPO_ROOT
                    ),

                env=
                    environment,

                **self._creation_options()
            )
        )


        state.process = (
            process
        )


        state.started_at = (
            time.monotonic()
        )


        print(
            "SUPERVISOR START | "
            f"worker={state.spec.name} | "
            f"pid={process.pid}",
            flush=True
        )


    def _schedule_restart(
        self,
        state
    ):

        now = (
            time.monotonic()
        )


        runtime = 0.0


        if (
            state.started_at
            is not None
        ):

            runtime = (

                now

                - state.started_at
            )


        if (
            runtime
            >= STABLE_RUNTIME_SECONDS
        ):

            state.restart_count = 0


        else:

            state.restart_count += 1


        restart_delay = min(

            MAX_RESTART_DELAY_SECONDS,

            2 ** min(
                state.restart_count,
                5
            )
        )


        state.process = None

        state.started_at = None

        state.next_start_at = (

            now

            + restart_delay
        )


        print(
            "SUPERVISOR RESTART SCHEDULED | "
            f"worker={state.spec.name} | "
            f"delay={restart_delay}s | "
            f"restart_count="
            f"{state.restart_count}",
            flush=True
        )


    def _check_workers(
        self
    ):

        now = (
            time.monotonic()
        )


        for state in self.states:

            process = (
                state.process
            )


            if (
                process
                is not None
            ):

                return_code = (
                    process.poll()
                )


                if (
                    return_code
                    is not None
                ):

                    print(
                        "SUPERVISOR EXIT | "
                        f"worker="
                        f"{state.spec.name} | "
                        f"pid="
                        f"{process.pid} | "
                        f"code="
                        f"{return_code}",
                        flush=True
                    )


                    self._schedule_restart(
                        state
                    )


                    continue


            if (
                state.process
                is None

                and

                now
                >= state.next_start_at
            ):

                self._start_worker(
                    state
                )


    @staticmethod
    def _signal_worker(
        process
    ):

        if (
            process.poll()
            is not None
        ):

            return


        if (
            os.name
            == "nt"
        ):

            process.send_signal(
                signal.CTRL_BREAK_EVENT
            )


        else:

            process.send_signal(
                signal.SIGTERM
            )


    def _stop_worker(
        self,
        state
    ):

        process = (
            state.process
        )


        if (
            process
            is None
        ):

            return


        if (
            process.poll()
            is not None
        ):

            return


        print(
            "SUPERVISOR STOP | "
            f"worker={state.spec.name} | "
            f"pid={process.pid}",
            flush=True
        )


        try:

            self._signal_worker(
                process
            )


            process.wait(
                timeout=
                    SHUTDOWN_TIMEOUT_SECONDS
            )


        except subprocess.TimeoutExpired:

            print(
                "SUPERVISOR FORCE KILL | "
                f"worker="
                f"{state.spec.name} | "
                f"pid="
                f"{process.pid}",
                flush=True
            )


            process.kill()


            process.wait()


        except ProcessLookupError:

            pass


    def shutdown(
        self
    ):

        print(
            "\nMarket data supervisor "
            "shutting down...",
            flush=True
        )


        for state in self.states:

            self._stop_worker(
                state
            )


        print(
            "Market data supervisor "
            "stopped cleanly.",
            flush=True
        )


    def run(
        self,
        run_seconds=None
    ):

        started = (
            time.monotonic()
        )


        print(
            "=" * 70,
            flush=True
        )


        print(
            "EUR/USD ORACLE "
            "MARKET DATA SUPERVISOR",
            flush=True
        )


        print(
            "=" * 70,
            flush=True
        )


        print(
            f"Python: "
            f"{sys.executable}",
            flush=True
        )


        print(
            f"Repository: "
            f"{REPO_ROOT}",
            flush=True
        )


        try:

            while not (
                self.stop_requested
            ):

                self._check_workers()


                if (
                    run_seconds
                    is not None
                ):

                    elapsed = (

                        time.monotonic()

                        - started
                    )


                    if (
                        elapsed
                        >= run_seconds
                    ):

                        print(
                            "Supervisor test "
                            "duration reached.",
                            flush=True
                        )

                        break


                time.sleep(
                    CHECK_INTERVAL_SECONDS
                )


        except KeyboardInterrupt:

            print(
                "\nSupervisor received "
                "shutdown request.",
                flush=True
            )


        finally:

            self.shutdown()


def parse_args():

    parser = (
        argparse.ArgumentParser(

            description=(
                "EUR/USD ORACLE market "
                "data process supervisor."
            )
        )
    )


    parser.add_argument(

        "--run-seconds",

        type=int,

        default=None,

        help=(
            "Optional finite runtime used "
            "for supervisor smoke tests."
        )
    )


    args = (
        parser.parse_args()
    )


    if (
        args.run_seconds
        is not None

        and

        args.run_seconds
        <= 0
    ):

        parser.error(
            "--run-seconds must be "
            "greater than zero."
        )


    return args


def main():

    args = (
        parse_args()
    )


    lock = (
        SingleInstanceLock(
            "market_data_supervisor"
        )
    )


    if not lock.acquire():

        print(
            "Another ORACLE market data "
            "supervisor is already running.",
            flush=True
        )

        return


    try:

        supervisor = (
            MarketDataSupervisor()
        )


        supervisor.run(
            run_seconds=
                args.run_seconds
        )


    finally:

        lock.release()


if __name__ == "__main__":

    main()
    