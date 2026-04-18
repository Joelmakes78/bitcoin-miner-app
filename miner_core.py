import hashlib
import struct
import socket
import json
import time
import threading
import multiprocessing
import logging
from typing import Optional, Callable

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class StratumClient:
    """Full Stratum v1 protocol client for connecting to mining pools."""

    def __init__(self, pool_host: str, pool_port: int, wallet_address: str,
                 worker_name: str = "worker1", password: str = "x"):
        self.pool_host = pool_host
        self.pool_port = pool_port
        self.wallet_address = wallet_address
        self.worker_name = worker_name
        self.password = password

        self.sock: Optional[socket.socket] = None
        self.msg_id = 0
        self.subscribed = False
        self.authorized = False
        self.extranonce1 = ""
        self.extranonce2_size = 4
        self.current_job: Optional[dict] = None
        self.lock = threading.Lock()
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None
        self.on_job_received: Optional[Callable] = None
        self.on_share_accepted: Optional[Callable] = None
        self.on_share_rejected: Optional[Callable] = None

    def connect(self) -> bool:
        """Establish TCP connection to the mining pool."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(30)
            self.sock.connect((self.pool_host, self.pool_port))
            self._running = True
            logger.info(f"Connected to pool {self.pool_host}:{self.pool_port}")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def disconnect(self):
        """Close the connection."""
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        logger.info("Disconnected from pool.")

    def _send(self, payload: dict):
        """Send a JSON-RPC message to the pool."""
        with self.lock:
            self.msg_id += 1
            payload["id"] = self.msg_id
        message = json.dumps(payload) + "\n"
        try:
            self.sock.sendall(message.encode("utf-8"))
            logger.debug(f"Sent: {message.strip()}")
        except Exception as e:
            logger.error(f"Send error: {e}")

    def _recv_line(self) -> Optional[dict]:
        """Receive a single newline-terminated JSON message from the pool."""
        buffer = b""
        try:
            while self._running:
                chunk = self.sock.recv(1)
                if not chunk:
                    break
                buffer += chunk
                if chunk == b"\n":
                    return json.loads(buffer.decode("utf-8").strip())
        except Exception as e:
            logger.error(f"Receive error: {e}")
        return None

    def _recv_loop(self):
        """Background thread: continuously receive and handle pool messages."""
        while self._running:
            msg = self._recv_line()
            if msg is None:
                continue
            self._handle_message(msg)

    def _handle_message(self, msg: dict):
        """Dispatch incoming pool messages."""
        logger.debug(f"Received: {msg}")

        method = msg.get("method")
        if method == "mining.notify":
            params = msg.get("params", [])
            self.current_job = {
                "job_id":        params[0],
                "prevhash":      params[1],
                "coinb1":        params[2],
                "coinb2":        params[3],
                "merkle_branch": params[4],
                "version":       params[5],
                "nbits":         params[6],
                "ntime":         params[7],
                "clean_jobs":    params[8],
            }
            logger.info(f"New job received: {self.current_job['job_id']}")
            if self.on_job_received:
                self.on_job_received(self.current_job)

        elif method == "mining.set_difficulty":
            difficulty = msg["params"][0]
            logger.info(f"Pool set difficulty: {difficulty}")

        else:
            result = msg.get("result")
            error  = msg.get("error")

            if error:
                logger.error(f"Pool error: {error}")
                return

            # Subscribe response: [[...], extranonce1, extranonce2_size]
            if isinstance(result, list) and len(result) == 3:
                self.extranonce1      = result[1]
                self.extranonce2_size = result[2]
                self.subscribed       = True
                logger.info(f"Subscribed. extranonce1={self.extranonce1}")

            elif result is True:
                if not self.authorized:
                    self.authorized = True
                    logger.info("Authorized successfully.")
                else:
                    logger.info("Share accepted!")
                    if self.on_share_accepted:
                        self.on_share_accepted()

            elif result is False:
                logger.warning("Share rejected or auth failed.")
                if self.on_share_rejected:
                    self.on_share_rejected()

    def subscribe(self):
        self._send({"method": "mining.subscribe", "params": ["python-miner/1.0"]})

    def authorize(self):
        self._send({
            "method": "mining.authorize",
            "params": [f"{self.wallet_address}.{self.worker_name}", self.password]
        })

    def submit_share(self, job_id: str, extranonce2: str, ntime: str, nonce: str):
        self._send({
            "method": "mining.submit",
            "params": [
                f"{self.wallet_address}.{self.worker_name}",
                job_id, extranonce2, ntime, nonce
            ]
        })
        logger.info(f"Share submitted: job={job_id} nonce={nonce}")

    def start_listening(self):
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def handshake(self) -> bool:
        self.subscribe()
        time.sleep(1)
        self.authorize()
        time.sleep(1)
        return self.subscribed and self.authorized


class Miner:
    """Core Bitcoin miner: SHA256 hashing, nonce search, share submission."""

    def __init__(self, wallet_address: str, pool_host: str, pool_port: int,
                 worker_name: str = "worker1", password: str = "x",
                 num_threads: int = None):
        self.wallet_address = wallet_address
        self.pool_host      = pool_host
        self.pool_port      = pool_port
        self.worker_name    = worker_name
        self.password       = password
        self.num_threads    = num_threads or multiprocessing.cpu_count()

        self.stratum = StratumClient(pool_host, pool_port, wallet_address, worker_name, password)
        self.stratum.on_job_received   = self._on_new_job
        self.stratum.on_share_accepted = self._on_share_accepted
        self.stratum.on_share_rejected = self._on_share_rejected

        self._running        = False
        self._mine_threads: list = []
        self._current_job: Optional[dict] = None
        self._job_lock       = threading.Lock()

        # Stats
        self.hashes_done     = 0
        self.shares_accepted = 0
        self.shares_rejected = 0
        self.start_time: Optional[float] = None
        self._stats_lock     = threading.Lock()

        # Optional UI callbacks
        self.on_hashrate_update: Optional[Callable] = None
        self.on_status_update:   Optional[Callable] = None

    # ------------------------------------------------------------------ #
    #  Static hashing helpers                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def double_sha256(data: bytes) -> bytes:
        return hashlib.sha256(hashlib.sha256(data).digest()).digest()

    @staticmethod
    def build_coinbase(coinb1: str, extranonce1: str, extranonce2: str, coinb2: str) -> bytes:
        return bytes.fromhex(coinb1 + extranonce1 + extranonce2 + coinb2)

    @staticmethod
    def build_merkle_root(coinbase_hash: bytes, merkle_branch: list) -> bytes:
        root = coinbase_hash
        for branch in merkle_branch:
            root = Miner.double_sha256(root + bytes.fromhex(branch))
        return root

    @staticmethod
    def build_block_header(version: str, prevhash: str, merkle_root: bytes,
                           ntime: str, nbits: str, nonce: int) -> bytes:
        return (
            struct.pack("<I", int(version, 16)) +
            bytes.fromhex(prevhash)[::-1] +
            merkle_root[::-1] +
            bytes.fromhex(ntime)[::-1] +
            bytes.fromhex(nbits)[::-1] +
            struct.pack("<I", nonce)
        )

    @staticmethod
    def nbits_to_target(nbits: str) -> int:
        n = int(nbits, 16)
        exp      = n >> 24
        mantissa = n & 0xFFFFFF
        return mantissa * (2 ** (8 * (exp - 3)))

    @staticmethod
    def meets_target(header_hash: bytes, nbits: str) -> bool:
        return int.from_bytes(header_hash, "big") <= Miner.nbits_to_target(nbits)

    # ------------------------------------------------------------------ #
    #  Callbacks                                                          #
    # ------------------------------------------------------------------ #

    def _on_new_job(self, job: dict):
        with self._job_lock:
            self._current_job = job
        if self.on_status_update:
            self.on_status_update(f"New job: {job['job_id']}")

    def _on_share_accepted(self):
        with self._stats_lock:
            self.shares_accepted += 1
        if self.on_status_update:
            self.on_status_update(f"✅ Share accepted! Total: {self.shares_accepted}")

    def _on_share_rejected(self):
        with self._stats_lock:
            self.shares_rejected += 1
        if self.on_status_update:
            self.on_status_update(f"❌ Share rejected. Total: {self.shares_rejected}")

    # ------------------------------------------------------------------ #
    #  Worker thread                                                      #
    # ------------------------------------------------------------------ #

    def _mine_worker(self, thread_id: int, nonce_start: int, nonce_step: int):
        """Hash loop: each thread works on its own nonce slice."""
        logger.info(f"Worker {thread_id} started (start={nonce_start}, step={nonce_step})")
        extranonce2 = format(thread_id, f"0{self.stratum.extranonce2_size * 2}x")

        while self._running:
            with self._job_lock:
                job = self._current_job

            if job is None:
                time.sleep(0.1)
                continue

            try:
                coinbase     = self.build_coinbase(job["coinb1"], self.stratum.extranonce1,
                                                   extranonce2, job["coinb2"])
                cb_hash      = self.double_sha256(coinbase)
                merkle_root  = self.build_merkle_root(cb_hash, job["merkle_branch"])

                nonce        = nonce_start
                local_hashes = 0

                while self._running and nonce < 2**32:
                    # Check for new job
                    with self._job_lock:
                        live_job_id = self._current_job["job_id"] if self._current_job else None
                    if live_job_id != job["job_id"]:
                        break

                    header      = self.build_block_header(job["version"], job["prevhash"],
                                                          merkle_root, job["ntime"],
                                                          job["nbits"], nonce)
                    header_hash = self.double_sha256(header)[::-1]

                    if self.meets_target(header_hash, job["nbits"]):
                        nonce_hex = format(nonce, "08x")
                        logger.info(f"🎉 Share found! nonce={nonce_hex} hash={header_hash.hex()}")
                        self.stratum.submit_share(job["job_id"], extranonce2,
                                                  job["ntime"], nonce_hex)

                    nonce        += nonce_step
                    local_hashes += 1

                    # Batch-update global counter and fire callback
                    if local_hashes % 50_000 == 0:
                        with self._stats_lock:
                            self.hashes_done += local_hashes
                        local_hashes = 0
                        if self.on_hashrate_update and self.start_time:
                            elapsed = time.time() - self.start_time
                            self.on_hashrate_update(self.hashes_done / elapsed if elapsed else 0)

                with self._stats_lock:
                    self.hashes_done += local_hashes

            except Exception as e:
                logger.error(f"Worker {thread_id} error: {e}")
                time.sleep(1)

    # ------------------------------------------------------------------ #
    #  Stats reporter                                                     #
    # ------------------------------------------------------------------ #

    def _stats_reporter(self):
        while self._running:
            time.sleep(10)
            s = self.get_stats()
            logger.info(
                f"📊 Hashrate: {s['hashrate']:.1f} H/s | "
                f"Accepted: {s['shares_accepted']} | "
                f"Rejected: {s['shares_rejected']} | "
                f"Uptime: {s['uptime']}s"
            )

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def start(self) -> bool:
        """Connect to pool and begin mining."""
        logger.info("Starting miner...")
        if self.on_status_update:
            self.on_status_update("Connecting to pool…")

        if not self.stratum.connect():
            if self.on_status_update:
                self.on_status_update("❌ Connection failed!")
            return False

        self.stratum.start_listening()
        time.sleep(0.5)

        if not self.stratum.handshake():
            if self.on_status_update:
                self.on_status_update("❌ Handshake failed!")
            return False

        self._running    = True
        self.start_time  = time.time()
        self._mine_threads.clear()

        for i in range(self.num_threads):
            t = threading.Thread(target=self._mine_worker, args=(i, i, self.num_threads), daemon=True)
            t.start()
            self._mine_threads.append(t)

        threading.Thread(target=self._stats_reporter, daemon=True).start()

        if self.on_status_update:
            self.on_status_update(f"⛏️  Mining with {self.num_threads} threads…")
        logger.info(f"Mining started with {self.num_threads} threads.")
        return True

    def stop(self):
        """Stop all threads and disconnect from pool."""
        logger.info("Stopping miner…")
        self._running = False
        self.stratum.disconnect()
        for t in self._mine_threads:
            t.join(timeout=2)
        self._mine_threads.clear()
        if self.on_status_update:
            self.on_status_update("🛑 Miner stopped.")
        logger.info("Miner stopped.")

    def get_stats(self) -> dict:
        elapsed = time.time() - (self.start_time or time.time())
        with self._stats_lock:
            return {
                "hashrate":        self.hashes_done / elapsed if elapsed > 0 else 0,
                "hashes_done":     self.hashes_done,
                "shares_accepted": self.shares_accepted,
                "shares_rejected": self.shares_rejected,
                "uptime":          int(elapsed),
                "threads":         self.num_threads,
            }