#!/usr/bin/env python3
"""
ULTRA FAST Bitcoin Miner - Multiprocessing optimized for maximum hashrate
Uses multiprocessing for true parallel execution across all CPU cores.
"""

import os
import hashlib
import time
import struct
import threading
import socket
import json
import logging
import multiprocessing as mp
from multiprocessing import Process, Queue, Value, Lock
from typing import Optional
import ctypes

# =============================================================================
# CONFIGURATION - Edit these values
# =============================================================================
WALLET_ADDRESS = "1HwQuxoGiUciUKE99NDJifNBpRp63er4Nw"
WORKER_NAME     = "UltraMiner"
POOL_HOST       = "btc.f2pool.com"  # F2Pool - View stats at https://www.f2pool.com
POOL_PORT       = 3333
PASSWORD        = "x"
NUM_WORKERS     = mp.cpu_count()  # One process per core

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# =============================================================================
# SHARED STATE (for multiprocessing)
# =============================================================================
class SharedState:
    """Shared state between processes using shared memory."""
    
    def __init__(self):
        # Use raw shared memory for maximum performance
        self.hashes_total = mp.Value(ctypes.c_ulonglong, 0)
        self.shares_found = mp.Value(ctypes.c_uint, 0)
        self.running = mp.Value(ctypes.c_bool, True)
        self.difficulty = mp.Value(ctypes.c_double, 1.0)
        self.lock = mp.Lock()

    def add_hashes(self, count: int):
        with self.lock:
            self.hashes_total.value += count

    def add_share(self):
        with self.lock:
            self.shares_found.value += 1

    def get_stats(self):
        with self.lock:
            return self.hashes_total.value, self.shares_found.value

    def stop(self):
        self.running.value = False

    def is_running(self) -> bool:
        return self.running.value


# =============================================================================
# FAST HASH FUNCTIONS
# =============================================================================
def sha256d(data: bytes) -> bytes:
    """Double SHA256 - Bitcoin's hash function."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def difficulty_to_target(difficulty: float) -> int:
    """Convert pool difficulty to target."""
    DIFF1_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000
    return DIFF1_TARGET // int(max(difficulty, 1))


# =============================================================================
# STRATUM CLIENT (runs in main process)
# =============================================================================
class StratumManager:
    """Manages stratum connection and distributes work to miner processes."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 job_queue: Queue, share_queue: Queue, shared_state: SharedState):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        
        self.job_queue = job_queue
        self.share_queue = share_queue
        self.shared_state = shared_state
        
        self.sock: Optional[socket.socket] = None
        self.msg_id = 0
        self.lock = threading.Lock()
        
        self.extranonce1 = ""
        self.extranonce2_size = 4
        self.current_job = None
        self.target = difficulty_to_target(1)

    def connect(self) -> bool:
        """Connect to pool."""
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
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        logger.info("Disconnected from pool")

    def _send(self, msg: dict):
        with self.lock:
            self.msg_id += 1
            msg["id"] = self.msg_id
        self.sock.sendall((json.dumps(msg) + "\n").encode())

    def _recv_loop(self):
        """Receive messages from pool."""
        buf = b""
        while self.shared_state.is_running() and self.sock:
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
        if not line:
            return
        try:
            msg = json.loads(line)
        except:
            return

        method = msg.get("method")

        if method == "mining.notify":
            self._handle_job(msg.get("params", []))
        elif method == "mining.set_difficulty":
            diff = msg.get("params", [1])[0]
            logger.info(f"📊 Difficulty: {diff}")
            self.target = difficulty_to_target(diff)
            self.current_job['target'] = self.target if self.current_job else self.target

        elif msg.get("id"):
            result = msg.get("result")
            
            if msg["id"] == 1 and result:  # subscribe
                self.extranonce1 = result[1]
                self.extranonce2_size = result[2]
                logger.info(f"📥 Subscribed")
            elif msg["id"] == 2:  # authorize
                if result:
                    logger.info("🔐 Authorized")
                else:
                    logger.error("❌ Auth failed")
            elif result is True:
                logger.info("✅ Share accepted!")
            elif result is False:
                logger.warning("❌ Share rejected")

    def _handle_job(self, params: list):
        """Handle new mining job - distribute to all workers."""
        if len(params) < 9:
            return
        
        job_id, prevhash, coinb1, coinb2, merkle_branches, version, nbits, ntime, clean = params
        
        self.current_job = {
            'job_id': job_id,
            'prevhash': prevhash,
            'coinb1': coinb1,
            'coinb2': coinb2,
            'merkle_branches': merkle_branches or [],
            'version': version,
            'nbits': nbits,
            'ntime': ntime,
            'target': self.target,
            'extranonce1': self.extranonce1,
            'extranonce2_size': self.extranonce2_size,
        }
        
        logger.info(f"🔨 New job: {job_id}")
        
        # Send job to all worker processes
        for _ in range(NUM_WORKERS):
            try:
                self.job_queue.put_nowait(self.current_job)
            except:
                pass

    def submit_share(self, job_id: str, extranonce2: int, ntime: str, nonce: int):
        """Submit share to pool."""
        en2_hex = extranonce2.to_bytes(self.extranonce2_size, 'big').hex()
        self._send({
            "method": "mining.submit",
            "params": [self.user, job_id, en2_hex, ntime, format(nonce, '08x')]
        })

    def share_submitter(self):
        """Thread to handle share submissions from workers."""
        while self.shared_state.is_running():
            try:
                share = self.share_queue.get(timeout=1)
                if share:
                    self.submit_share(**share)
            except:
                pass

    def start(self) -> bool:
        """Start the stratum manager."""
        if not self.connect():
            return False
        
        self._send({"method": "mining.subscribe", "params": []})
        time.sleep(0.5)
        self._send({"method": "mining.authorize", "params": [self.user, self.password]})
        time.sleep(0.5)
        
        # Start receiver thread
        threading.Thread(target=self._recv_loop, daemon=True).start()
        
        # Start share submitter thread
        threading.Thread(target=self.share_submitter, daemon=True).start()
        
        return True


# =============================================================================
# MINER WORKER PROCESS
# =============================================================================
def miner_worker(worker_id: int, job_queue: Queue, share_queue: Queue, 
                 shared_state: SharedState):
    """Worker process - mines nonces independently."""
    
    logger.info(f"🧵 Worker {worker_id} started (PID: {os.getpid()})")
    
    current_job = None
    job_id = None
    extranonce2_base = worker_id * 1000000  # Unique range per worker
    
    while shared_state.is_running():
        # Check for new job
        try:
            new_job = job_queue.get_nowait()
            if new_job:
                current_job = new_job
                job_id = new_job['job_id']
        except:
            pass
        
        if not current_job:
            time.sleep(0.01)
            continue
        
        # Pre-compute static parts
        extranonce2 = extranonce2_base
        en1_bytes = bytes.fromhex(current_job['extranonce1'])
        en2_bytes = extranonce2.to_bytes(4, 'big')
        coinbase = (bytes.fromhex(current_job['coinb1']) + 
                   en1_bytes + en2_bytes + 
                   bytes.fromhex(current_job['coinb2']))
        coinbase_hash = sha256d(coinbase)
        
        # Build merkle root
        merkle_root = coinbase_hash
        for branch in current_job['merkle_branches']:
            merkle_root = sha256d(merkle_root + bytes.fromhex(branch))
        
        # Pre-compute header prefix (72 bytes)
        version_bytes = struct.pack("<I", int(current_job['version'], 16))
        prevhash_bytes = bytes.fromhex(current_job['prevhash'])
        ntime_bytes = struct.pack("<I", int(current_job['ntime'], 16))
        nbits_bytes = struct.pack("<I", int(current_job['nbits'], 16))
        target = current_job['target']
        
        header_prefix = version_bytes + prevhash_bytes + merkle_root + ntime_bytes + nbits_bytes
        
        # MINE - Hot loop
        nonce_start = worker_id
        nonce_step = NUM_WORKERS
        local_hashes = 0
        
        for nonce in range(nonce_start, 0xFFFFFFFF, nonce_step):
            if not shared_state.is_running():
                break
            
            # Check for new job periodically
            if nonce % 50000 == 0:
                try:
                    new_job = job_queue.get_nowait()
                    if new_job and new_job['job_id'] != job_id:
                        break  # New job, restart loop
                except:
                    pass
            
            # Build full header and hash
            header = header_prefix + struct.pack("<I", nonce)
            hash_result = sha256d(header)
            hash_int = int.from_bytes(hash_result, 'little')
            
            local_hashes += 1
            
            # Check target
            if hash_int < target:
                # Found a share!
                share_queue.put({
                    'job_id': current_job['job_id'],
                    'extranonce2': extranonce2,
                    'ntime': current_job['ntime'],
                    'nonce': nonce
                })
                shared_state.add_share()
                logger.info(f"🎉 Worker {worker_id} found share! nonce={nonce:08x}")
            
            # Update global hash counter periodically
            if local_hashes >= 100000:
                shared_state.add_hashes(local_hashes)
                local_hashes = 0
        
        # Flush remaining hashes
        if local_hashes > 0:
            shared_state.add_hashes(local_hashes)
    
    logger.info(f"🧵 Worker {worker_id} stopped")


# =============================================================================
# MAIN MINER CLASS
# =============================================================================
class UltraMiner:
    """Ultra-fast multiprocessing Bitcoin miner."""
    
    def __init__(self, wallet: str, worker: str, pool_host: str, pool_port: int,
                 num_workers: int = None, password: str = "x"):
        self.wallet = wallet
        self.worker = worker
        self.pool_host = pool_host
        self.pool_port = pool_port
        self.password = password
        self.num_workers = num_workers or mp.cpu_count()
        
        self.shared_state = SharedState()
        self.job_queue = Queue()
        self.share_queue = Queue()
        self.workers = []
        
        user = f"{wallet}.{worker}"
        self.stratum = StratumManager(
            pool_host, pool_port, user, password,
            self.job_queue, self.share_queue, self.shared_state
        )
        
        self.start_time = None

    def start(self) -> bool:
        """Start the miner."""
        logger.info("🚀 Starting ULTRA FAST Bitcoin Miner...")
        logger.info(f"📋 Wallet: {self.wallet}")
        logger.info(f"🔗 Pool: {self.pool_host}:{self.pool_port}")
        logger.info(f"🧵 Workers: {self.num_workers} processes")
        logger.info(f"💻 CPU cores: {mp.cpu_count()}")
        
        if not self.stratum.start():
            logger.error("❌ Failed to connect to pool")
            return False
        
        self.start_time = time.time()
        
        # Start worker processes
        for i in range(self.num_workers):
            p = Process(
                target=miner_worker,
                args=(i, self.job_queue, self.share_queue, self.shared_state),
                daemon=True
            )
            p.start()
            self.workers.append(p)
        
        # Start stats reporter
        threading.Thread(target=self._stats_reporter, daemon=True).start()
        
        logger.info("⛏️ Mining started! Press Ctrl+C to stop.")
        return True

    def _stats_reporter(self):
        """Report hashrate periodically."""
        last_hashes = 0
        last_time = time.time()
        
        while self.shared_state.is_running():
            time.sleep(10)
            
            now = time.time()
            hashes, shares = self.shared_state.get_stats()
            
            elapsed = now - last_time
            rate = (hashes - last_hashes) / elapsed if elapsed > 0 else 0
            last_hashes = hashes
            last_time = now
            
            uptime = int(now - self.start_time) if self.start_time else 0
            logger.info(
                f"⚡ {rate/1000:.2f} kH/s | "
                f"📊 {hashes:,} hashes | "
                f"⏱️ {uptime}s | "
                f"🎯 {shares} shares"
            )

    def stop(self):
        """Stop the miner."""
        logger.info("🛑 Stopping miner...")
        self.shared_state.stop()
        self.stratum.disconnect()
        
        for w in self.workers:
            w.join(timeout=2)
        
        elapsed = time.time() - self.start_time if self.start_time else 0
        hashes, shares = self.shared_state.get_stats()
        rate = hashes / elapsed if elapsed > 0 else 0
        
        logger.info(
            f"📊 Final: {rate/1000:.2f} kH/s avg, "
            f"{hashes:,} total hashes, {shares} shares"
        )

    def wait(self):
        """Wait while mining."""
        try:
            while self.shared_state.is_running():
                time.sleep(1)
        except KeyboardInterrupt:
            pass


# =============================================================================
# MAIN
# =============================================================================
def main():
    print("=" * 60)
    print("   ⚡ ULTRA FAST BITCOIN MINER v1.0")
    print("   Multiprocessing Optimized for Maximum Hashrate")
    print("=" * 60)
    
    miner = UltraMiner(
        wallet=WALLET_ADDRESS,
        worker=WORKER_NAME,
        pool_host=POOL_HOST,
        pool_port=POOL_PORT,
        num_workers=NUM_WORKERS,
        password=PASSWORD
    )
    
    try:
        if miner.start():
            miner.wait()
    except KeyboardInterrupt:
        print("\n")
    finally:
        miner.stop()
        print("👋 Goodbye!")
    
    return miner


if __name__ == "__main__":
    main()