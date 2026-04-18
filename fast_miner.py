#!/usr/bin/env python3
"""
Fast Bitcoin Miner - Multi-threaded optimized Stratum miner
Usage: python fast_miner.py
"""

import os
import hashlib
import time
import struct
import threading
import socket
import json
import logging
import multiprocessing
from typing import Optional, Dict, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

# =============================================================================
# CONFIGURATION - Edit these values
# =============================================================================
WALLET_ADDRESS = "1HwQuxoGiUciUKE99NDJifNBpRp63er4Nw"
WORKER_NAME     = "SoloWorker"
POOL_HOST       = "solo.ckpool.org"
POOL_PORT       = 3333
PASSWORD        = "x"
LOG_PATH        = "miner_log.txt"
NUM_THREADS     = multiprocessing.cpu_count()  # Auto-detect cores

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# DATA CLASSES
# =============================================================================
@dataclass
class MiningJob:
    job_id: str
    prevhash: str
    coinb1: str
    coinb2: str
    merkle_branches: list
    version: str
    nbits: str
    ntime: str
    target: int
    clean: bool

    # Pre-computed values for speed
    version_bytes: bytes = b''
    prevhash_bytes: bytes = b''
    ntime_bytes: bytes = b''
    nbits_bytes: bytes = b''
    merkle_root: bytes = b''
    coinbase_prefix: bytes = b''
    coinbase_suffix: bytes = b''
    extranonce1_bytes: bytes = b''

    def __post_init__(self):
        """Pre-compute static parts of block header for faster mining."""
        self.version_bytes = struct.pack("<I", int(self.version, 16))
        self.prevhash_bytes = bytes.fromhex(self.prevhash)
        self.ntime_bytes = struct.pack("<I", int(self.ntime, 16))
        self.nbits_bytes = struct.pack("<I", int(self.nbits, 16))

    def build_merkle_root(self, coinbase_hash: bytes) -> bytes:
        """Build merkle root from coinbase hash and branches."""
        root = coinbase_hash
        for branch in self.merkle_branches:
            root = hashlib.sha256(hashlib.sha256(root + bytes.fromhex(branch)).digest()).digest()
        return root

    def precompute_coinbase_parts(self, extranonce1: str, extranonce2_size: int):
        """Pre-compute coinbase parts that don't change between nonces."""
        self.extranonce1_bytes = bytes.fromhex(extranonce1)
        self.coinbase_prefix = bytes.fromhex(self.coinb1)
        self.coinbase_suffix = bytes.fromhex(self.coinb2)

    def build_header(self, extranonce2: int, nonce: int) -> bytes:
        """Build 80-byte block header. Optimized for speed."""
        # Build coinbase
        en2_bytes = extranonce2.to_bytes(4, 'big')
        coinbase = self.coinbase_prefix + self.extranonce1_bytes + en2_bytes + self.coinbase_suffix
        coinbase_hash = hashlib.sha256(hashlib.sha256(coinbase).digest()).digest()
        
        # Build merkle root (cached if no branches)
        if not self.merkle_root:
            self.merkle_root = self.build_merkle_root(coinbase_hash)
        else:
            # Rebuild for each extranonce2
            self.merkle_root = self.build_merkle_root(coinbase_hash)
        
        # Build header
        return (
            self.version_bytes +
            self.prevhash_bytes +
            self.merkle_root +
            self.ntime_bytes +
            self.nbits_bytes +
            struct.pack("<I", nonce)
        )


# =============================================================================
# FAST HASH FUNCTIONS
# =============================================================================
def sha256d(data: bytes) -> bytes:
    """Double SHA256 - the Bitcoin hash function."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def difficulty_to_target(difficulty: float) -> int:
    """Convert pool difficulty to target threshold."""
    # max_target for difficulty 1
    DIFF1_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000
    if difficulty == 0:
        return DIFF1_TARGET
    return DIFF1_TARGET // int(difficulty)


# =============================================================================
# STRATUM CLIENT
# =============================================================================
class StratumClient:
    """Stratum protocol client for mining pool communication."""

    def __init__(self, host: str, port: int, user: str, password: str = "x"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password

        self.sock: Optional[socket.socket] = None
        self.msg_id = 0
        self.lock = threading.Lock()

        # Pool state
        self.extranonce1 = ""
        self.extranonce2_size = 4
        self.subscribed = False
        self.authorized = False

        # Callbacks
        self.on_job: Optional[callable] = None
        self.on_difficulty: Optional[callable] = None

    def connect(self) -> bool:
        """Connect to the mining pool."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(60)
            self.sock.connect((self.host, self.port))
            logger.info(f"✅ Connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from pool."""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        logger.info("Disconnected from pool")

    def _send(self, msg: dict):
        """Send JSON-RPC message."""
        with self.lock:
            self.msg_id += 1
            msg["id"] = self.msg_id
        data = json.dumps(msg) + "\n"
        self.sock.sendall(data.encode())

    def _recv_loop(self):
        """Receive and dispatch messages from pool."""
        buf = b""
        while self.sock:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_message(line.decode().strip())
            except Exception as e:
                logger.error(f"Receive error: {e}")
                break

    def _handle_message(self, line: str):
        """Handle a single JSON message from pool."""
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return

        method = msg.get("method")

        # Mining notifications
        if method == "mining.notify":
            self._handle_notify(msg.get("params", []))
        elif method == "mining.set_difficulty":
            difficulty = msg.get("params", [1])[0]
            logger.info(f"📊 Difficulty set to {difficulty}")
            if self.on_difficulty:
                self.on_difficulty(difficulty)

        # Responses
        elif msg.get("id"):
            result = msg.get("result")
            error = msg.get("error")

            if msg["id"] == 1:  # subscribe response
                if result and isinstance(result, list):
                    self.extranonce1 = result[1]
                    self.extranonce2_size = result[2]
                    self.subscribed = True
                    logger.info(f"📥 Subscribed. extranonce1={self.extranonce1[:16]}...")

            elif msg["id"] == 2:  # authorize response
                if result:
                    self.authorized = True
                    logger.info("🔐 Authorized successfully")
                else:
                    logger.error(f"❌ Auth failed: {error}")

            elif result is True:
                logger.info(f"✅ Share accepted!")
            elif result is False or error:
                logger.warning(f"❌ Share rejected: {error}")

    def _handle_notify(self, params: list):
        """Handle mining.notify - new job from pool."""
        if len(params) < 9:
            return

        job_id, prevhash, coinb1, coinb2, merkle_branches, version, nbits, ntime, clean = params

        job = MiningJob(
            job_id=job_id,
            prevhash=prevhash,
            coinb1=coinb1,
            coinb2=coinb2,
            merkle_branches=merkle_branches or [],
            version=version,
            nbits=nbits,
            ntime=ntime,
            target=0,  # Will be set by difficulty callback
            clean=bool(clean)
        )
        job.precompute_coinbase_parts(self.extranonce1, self.extranonce2_size)

        logger.info(f"🔨 New job: {job_id}")
        if self.on_job:
            self.on_job(job)

    def subscribe(self):
        """Send mining.subscribe."""
        self._send({"method": "mining.subscribe", "params": []})

    def authorize(self):
        """Send mining.authorize."""
        self._send({
            "method": "mining.authorize",
            "params": [self.user, self.password]
        })

    def submit_share(self, job_id: str, extranonce2: int, ntime: str, nonce: int):
        """Submit a found share to the pool."""
        en2_hex = extranonce2.to_bytes(self.extranonce2_size, 'big').hex()
        self._send({
            "method": "mining.submit",
            "params": [
                self.user,
                job_id,
                en2_hex,
                ntime,
                format(nonce, '08x')
            ]
        })

    def start(self):
        """Start the stratum client."""
        if not self.connect():
            return False
        self.subscribe()
        time.sleep(0.5)
        self.authorize()
        time.sleep(0.5)
        
        # Start receiver thread
        threading.Thread(target=self._recv_loop, daemon=True).start()
        return self.subscribed and self.authorized


# =============================================================================
# FAST MINER
# =============================================================================
class FastMiner:
    """High-performance multi-threaded Bitcoin miner."""

    def __init__(self, wallet: str, worker: str, pool_host: str, pool_port: int,
                 num_threads: int = None, password: str = "x"):
        self.wallet = wallet
        self.worker = worker
        self.pool_host = pool_host
        self.pool_port = pool_port
        self.password = password
        self.num_threads = num_threads or multiprocessing.cpu_count()

        # Stratum client
        user = f"{wallet}.{worker}"
        self.stratum = StratumClient(pool_host, pool_port, user, password)
        self.stratum.on_job = self._on_new_job
        self.stratum.on_difficulty = self._on_difficulty

        # State
        self.running = False
        self.current_job: Optional[MiningJob] = None
        self.job_lock = threading.Lock()
        self.target = difficulty_to_target(1)

        # Stats
        self.hashes_total = 0
        self.shares_found = 0
        self.start_time: Optional[float] = None
        self.stats_lock = threading.Lock()

        # Callbacks for UI
        self.on_hashrate: Optional[callable] = None
        self.on_share: Optional[callable] = None
        self.on_status: Optional[callable] = None

    def _on_new_job(self, job: MiningJob):
        """Handle new job from pool."""
        job.target = self.target
        with self.job_lock:
            self.current_job = job

    def _on_difficulty(self, difficulty: float):
        """Handle difficulty change from pool."""
        self.target = difficulty_to_target(difficulty)
        with self.job_lock:
            if self.current_job:
                self.current_job.target = self.target

    def _mine_worker(self, thread_id: int):
        """Worker thread - mines nonces in its assigned range."""
        logger.info(f"🧵 Thread {thread_id} started")
        
        extranonce2 = thread_id  # Each thread gets unique extranonce2 range
        local_hashes = 0
        last_report = time.time()

        while self.running:
            # Get current job
            with self.job_lock:
                job = self.current_job
                job_id = job.job_id if job else None

            if not job:
                time.sleep(0.1)
                continue

            # Pre-compute coinbase and merkle for this extranonce2
            en2_bytes = extranonce2.to_bytes(4, 'big')
            coinbase = job.coinbase_prefix + job.extranonce1_bytes + en2_bytes + job.coinbase_suffix
            coinbase_hash = sha256d(coinbase)
            
            # Build merkle root
            merkle_root = coinbase_hash
            for branch in job.merkle_branches:
                merkle_root = sha256d(merkle_root + bytes.fromhex(branch))

            # Mine nonces - this is the hot loop
            nonce_start = thread_id
            nonce_step = self.num_threads

            for nonce in range(nonce_start, 0xFFFFFFFF, nonce_step):
                if not self.running:
                    break

                # Check for new job (every 10000 nonces to reduce lock contention)
                if nonce % 10000 == 0:
                    with self.job_lock:
                        if self.current_job and self.current_job.job_id != job_id:
                            break  # New job, restart

                # Build header
                header = (
                    job.version_bytes +
                    job.prevhash_bytes +
                    merkle_root +
                    job.ntime_bytes +
                    job.nbits_bytes +
                    struct.pack("<I", nonce)
                )

                # Hash
                hash_result = sha256d(header)
                hash_int = int.from_bytes(hash_result, 'little')

                local_hashes += 1

                # Check if share found
                if hash_int < self.target:
                    self.stratum.submit_share(job.job_id, extranonce2, job.ntime, nonce)
                    with self.stats_lock:
                        self.shares_found += 1
                    logger.info(f"🎉 SHARE FOUND! Thread {thread_id} nonce={nonce:08x}")
                    if self.on_share:
                        self.on_share(nonce, hash_result[::-1].hex())

                # Update stats periodically
                if local_hashes % 50000 == 0:
                    with self.stats_lock:
                        self.hashes_total += local_hashes
                    local_hashes = 0

            # Increment extranonce2 for next iteration
            extranonce2 += self.num_threads

        # Final stats update
        with self.stats_lock:
            self.hashes_total += local_hashes
        logger.info(f"🧵 Thread {thread_id} stopped")

    def _stats_reporter(self):
        """Report hashrate periodically."""
        last_hashes = 0
        last_time = time.time()

        while self.running:
            time.sleep(5)
            
            now = time.time()
            with self.stats_lock:
                total = self.hashes_total
            
            elapsed = now - last_time
            rate = (total - last_hashes) / elapsed if elapsed > 0 else 0
            last_hashes = total
            last_time = now

            uptime = int(now - self.start_time) if self.start_time else 0
            
            logger.info(f"⚡ {rate/1000:.2f} kH/s | 📊 Total: {total:,} hashes | ⏱️ Uptime: {uptime}s | 🎯 Shares: {self.shares_found}")
            
            if self.on_hashrate:
                self.on_hashrate(rate)

    def start(self) -> bool:
        """Start the miner."""
        logger.info("🚀 Starting Fast Bitcoin Miner...")
        logger.info(f"📋 Wallet: {self.wallet}")
        logger.info(f"🔗 Pool: {self.pool_host}:{self.pool_port}")
        logger.info(f"🧵 Threads: {self.num_threads}")

        if self.on_status:
            self.on_status("Connecting to pool...")

        if not self.stratum.start():
            logger.error("❌ Failed to connect to pool")
            if self.on_status:
                self.on_status("Connection failed!")
            return False

        self.running = True
        self.start_time = time.time()

        if self.on_status:
            self.on_status(f"Mining with {self.num_threads} threads...")

        # Start mining threads
        for i in range(self.num_threads):
            t = threading.Thread(target=self._mine_worker, args=(i,), daemon=True)
            t.start()

        # Start stats reporter
        threading.Thread(target=self._stats_reporter, daemon=True).start()

        logger.info("⛏️ Mining started! Press Ctrl+C to stop.")
        return True

    def stop(self):
        """Stop the miner."""
        logger.info("🛑 Stopping miner...")
        self.running = False
        self.stratum.disconnect()

        elapsed = time.time() - self.start_time if self.start_time else 0
        rate = self.hashes_total / elapsed if elapsed > 0 else 0
        
        logger.info(f"📊 Final stats: {rate/1000:.2f} kH/s avg, {self.hashes_total:,} total hashes, {self.shares_found} shares")
        if self.on_status:
            self.on_status("Stopped")

    def get_stats(self) -> dict:
        """Get current mining statistics."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        with self.stats_lock:
            return {
                "hashrate": self.hashes_total / elapsed if elapsed > 0 else 0,
                "hashes_total": self.hashes_total,
                "shares_found": self.shares_found,
                "uptime": int(elapsed),
                "threads": self.num_threads
            }


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================
def main():
    """Main entry point."""
    print("=" * 60)
    print("   🚀 FAST BITCOIN MINER v1.0")
    print("   Multi-threaded Stratum Miner")
    print("=" * 60)

    miner = FastMiner(
        wallet=WALLET_ADDRESS,
        worker=WORKER_NAME,
        pool_host=POOL_HOST,
        pool_port=POOL_PORT,
        num_threads=NUM_THREADS,
        password=PASSWORD
    )

    try:
        if miner.start():
            # Keep running until interrupted
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n")
        miner.stop()
        print("👋 Goodbye!")

    return miner


if __name__ == "__main__":
    main()