import os
import tempfile

from pathlib import Path


class SingleInstanceLock:

    def __init__(
        self,
        name
    ):

        safe_name = "".join(

            character

            if (
                character.isalnum()
                or character in (
                    "-",
                    "_"
                )
            )

            else "_"

            for character
            in name
        )


        self.lock_directory = (

            Path(
                tempfile.gettempdir()
            )

            / "EURUSD_ORACLE"
            / "runtime"
        )


        self.lock_path = (

            self.lock_directory

            / f"{safe_name}.lock"
        )


        self._handle = None


    def acquire(
        self
    ):

        if (
            self._handle
            is not None
        ):

            return True


        self.lock_directory.mkdir(

            parents=True,

            exist_ok=True
        )


        handle = open(
            self.lock_path,
            "a+b"
        )


        # Windows byte-range locking
        # requires at least one byte
        # to exist in the file.
        handle.seek(
            0,
            os.SEEK_END
        )


        if (
            handle.tell()
            == 0
        ):

            handle.write(
                b"\0"
            )

            handle.flush()


        handle.seek(
            0
        )


        try:

            if (
                os.name
                == "nt"
            ):

                import msvcrt


                msvcrt.locking(

                    handle.fileno(),

                    msvcrt.LK_NBLCK,

                    1
                )


            else:

                import fcntl


                fcntl.flock(

                    handle.fileno(),

                    (
                        fcntl.LOCK_EX
                        |
                        fcntl.LOCK_NB
                    )
                )


        except (
            OSError,
            IOError
        ):

            handle.close()

            return False


        self._handle = (
            handle
        )


        return True


    def release(
        self
    ):

        if (
            self._handle
            is None
        ):

            return


        try:

            self._handle.seek(
                0
            )


            if (
                os.name
                == "nt"
            ):

                import msvcrt


                msvcrt.locking(

                    self._handle
                    .fileno(),

                    msvcrt.LK_UNLCK,

                    1
                )


            else:

                import fcntl


                fcntl.flock(

                    self._handle
                    .fileno(),

                    fcntl.LOCK_UN
                )


        finally:

            self._handle.close()

            self._handle = None


    def __enter__(
        self
    ):

        if not self.acquire():

            raise RuntimeError(
                "Runtime lock is already held: "
                f"{self.lock_path}"
            )


        return self


    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback
    ):

        self.release()

        return False