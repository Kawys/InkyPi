import json
import os
import pty
import subprocess
import threading
import time
from _queue import Empty
from queue import Queue
from enum import Enum
import logging
import re

LOGIN_URL_PATTERN = re.compile(r"https://[\x21-\x7A]+")
LOGIN_SUCCESS_PATTERN = re.compile(r"Successfully logged in", re.IGNORECASE)
ENCRYPTED_PATTERN = re.compile(r"Sync file is encrypted|Failed to decrypt sync file")
LOGIN_LINK_TIMEOUT = 10
ENV = os.environ.copy()
ENV['PATH'] = f"/home/karol/Projects/InkyPi/node_modules/.bin:{ENV['PATH']}"

class State(Enum):
    ERROR = 0
    LOGGED_OFF = 1
    ENCRYPTED = 2
    LOGGED_IN = 3

logger = logging.getLogger(__name__)


class Sp:
    """
    Interface for super-productivity-cli's 'sp' command
    """

    def __init__(self, timeout: int = LOGIN_LINK_TIMEOUT):
        self.timeout = timeout
        self.process = None
        self.output = []
        self._master_fd = None
        self._queue = None
        self.state = self._check_state()

    def send_encryption_key(self, key: str) -> bool:
        if self.state != State.ENCRYPTED: return False

        key = key.strip()
        try:
            result = subprocess.run(
                ["sp", "encrypt-key", key],
                capture_output=True,
                text=True,
                check=False,
                env=ENV
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.error(f"Failed to run encryption key command: {e}")
            return False
        if result.returncode != 0: return False

        self.get_state(force_check=True)
        return True

    def get_login_link(self) -> str | None:
        """Run `sp login` and return the dropbox authorization link it prints.

        Returns:
            The authorization URL, or None if the command could not be started,
            exited without printing a link, or did not print one in time.
        """
        if self.state != State.LOGGED_OFF: return None

        self.cancel()
        self.output = []

        # run the command on a pty, otherwise it block buffers its output and
        # the link only arrives once the command exits
        master_fd, slave_fd = pty.openpty()
        try:
            self.process = subprocess.Popen(
                ["sp", "login"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=ENV
            )
        except (OSError, subprocess.SubprocessError) as e:
            os.close(master_fd)
            self.process = None
            raise RuntimeError(f"Failed to run login command: {e}")
        finally:
            os.close(slave_fd)

        self._master_fd = master_fd

        # read the output on a worker thread so a hung command cannot block us
        self._queue = Queue()
        threading.Thread(
            target=self._read_output,
            args=(master_fd, self._queue),
            daemon=True
        ).start()

        deadline = time.monotonic() + self.timeout
        pending = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error("Timed out waiting for the login link")
                break
            try:
                chunk = self._queue.get(timeout=remaining)
            except Empty:
                logger.error("Timed out waiting for the login link")
                break
            if chunk is None:
                # the command closed its output without printing a link
                logger.error(f"Login command did not return a link: {''.join(self.output).strip()}")
                break

            self.output.append(chunk)
            # only scan complete lines so a link split across reads is not truncated
            pending += chunk
            lines, _, pending = pending.rpartition("\n")
            match = LOGIN_URL_PATTERN.search(lines)
            if match:
                logger.info("Generated dropbox login link, waiting for the token")
                return match.group(0)

        self.cancel()
        return None

    def send_login_token(self, token: str) -> bool:
        """Paste the authorization code into the `sp login` process from get_login_link().

        Args:
            token: The code copied from the dropbox authorization page.

        Returns:
            True if the command reported a successful login, False otherwise.
            The login process is finished either way, so a new attempt needs a
            fresh get_login_link() call.
        """
        if self.process is None or self._master_fd is None or self._queue is None:
            logger.error("No login in progress, call get_login_link() first")
            return False

        if self.process.poll() is not None:
            logger.error("The login command exited before the token was sent")
            self.cancel()
            return False

        token = token.strip()
        if not token:
            logger.error("No login token provided")
            self.cancel()
            return False

        try:
            os.write(self._master_fd, f"{token}\n".encode())
        except OSError as e:
            logger.error(f"Failed to send the login token: {e}")
            self.cancel()
            return False

        success = False
        received = ""
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error("Timed out waiting for the login result")
                break
            try:
                chunk = self._queue.get(timeout=remaining)
            except Empty:
                logger.error("Timed out waiting for the login result")
                break
            if chunk is None:
                # the command finished, fall back to its exit status
                success = self._wait_for_exit() == 0
                if not success:
                    logger.error(f"Login failed: {''.join(self.output).strip()}")
                break

            self.output.append(chunk)
            received += chunk
            if LOGIN_SUCCESS_PATTERN.search(received):
                success = True
                # let the command finish on its own so it can persist the tokens
                self._wait_for_exit()
                break

        self.cancel()
        if success:
            logger.info("Successfully logged in to dropbox")
            self.get_state(force_check=True)
        return success

    @staticmethod
    def _check_state() -> State:
        """Check whether the user is authenticated with the dropbox backend.

        Runs `sp --dropbox status --json`
        State.LOGGED_IN: the command succeeds and reports "backend": "dropbox" in its JSON output
        State.ENCRYPTED: the command fails and reports "Sync file is encrypted."
        State.LOGGED_OFF: the command fails in other ways
        State.ERROR: error occurred
        """
        try:
            result = subprocess.run(
                ["sp", "--dropbox", "status", "--json"],
                capture_output=True,
                text=True,
                check=False,
                env=ENV
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.error(f"Failed to run dropbox status command: {e}")
            return State.ERROR

        if result.returncode != 0:
            match = ENCRYPTED_PATTERN.search(result.stderr.strip())
            if match: return State.ENCRYPTED
            return State.LOGGED_OFF

        try:
            status = json.loads(result.stdout)
            if isinstance(status, dict) and status.get("backend") == "dropbox":
                return State.LOGGED_IN
        except json.JSONDecodeError:
            logger.warning(f"Could not parse dropbox status output: {result.stdout.strip()}")

        return State.ERROR

    def get_state(self, force_check=False) -> State:
        if force_check:
            self.state = self._check_state()
        return self.state

    def cancel(self):
        """Terminate the `sp login` process if it is still running."""
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        self._queue = None

    def _wait_for_exit(self):
        """Wait for the login command to exit and return its exit code, or None."""
        try:
            return self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("The login command did not exit on its own")
            return None

    @staticmethod
    def _read_output(master_fd, queue):
        """Push the process output onto the queue in chunks, then None at EOF."""
        try:
            while True:
                chunk = os.read(master_fd, 1024)
                if not chunk:
                    break
                queue.put(chunk.decode("utf-8", errors="replace").replace("\r\n", "\n"))
        except OSError:
            # the pty raises EIO once the child closes its end
            pass
        finally:
            queue.put(None)
