#!/usr/bin/env python3
"""
Bitcoin Miner Launcher
======================
Easy launcher for all miner variants.

Usage:
    python run.py              # Auto-detect best miner
    python run.py --gpu        # Force GPU miner
    python run.py --cpu        # Force CPU miner
    python run.py --gui        # Launch GUI
"""

import sys
import os
import argparse
import multiprocessing as mp

def check_gpu():
    """Check if GPU acceleration is available."""
    try:
        import pyopencl as cl
        platforms = cl.get_platforms()
        if platforms:
            for p in platforms:
                devices = p.get_devices()
                for d in devices:
                    if d.type == cl.device_type.GPU:
                        return True
    except:
        pass
    
    try:
        import pycuda.driver as cuda
        cuda.init()
        if cuda.Device.count() > 0:
            return True
    except:
        pass
    
    return False


def main():
    parser = argparse.ArgumentParser(description='Bitcoin Miner Launcher')
    parser.add_argument('--gpu', action='store_true', help='Force GPU miner')
    parser.add_argument('--cpu', action='store_true', help='Force CPU miner')
    parser.add_argument('--gui', action='store_true', help='Launch GUI')
    parser.add_argument('--wallet', type=str, help='Wallet address')
    parser.add_argument('--worker', type=str, default='Worker1', help='Worker name')
    parser.add_argument('--pool', type=str, default='solo.ckpool.org', help='Pool host')
    parser.add_argument('--port', type=int, default=3333, help='Pool port')
    parser.add_argument('--threads', type=int, default=mp.cpu_count(), help='Number of threads')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("   ⛏️  BITCOIN MINER LAUNCHER")
    print("=" * 60)
    
    # Check GPU availability
    has_gpu = check_gpu()
    print(f"🎮 GPU Available: {'Yes' if has_gpu else 'No'}")
    print(f"💻 CPU Cores: {mp.cpu_count()}")
    print()
    
    # Launch GUI if requested
    if args.gui:
        print("🖥️ Launching GUI...")
        os.system(f"{sys.executable} gui.py")
        return
    
    # Determine which miner to use
    use_gpu = args.gpu or (has_gpu and not args.cpu)
    
    if use_gpu and has_gpu:
        print("🚀 Launching WARP MINER (GPU-accelerated)...")
        print()
        
        # Update config if wallet provided
        if args.wallet:
            import warp_miner
            warp_miner.WALLET_ADDRESS = args.wallet
            warp_miner.WORKER_NAME = args.worker
            warp_miner.POOL_HOST = args.pool
            warp_miner.POOL_PORT = args.port
        
        os.system(f"{sys.executable} warp_miner.py")
    
    else:
        print("🚀 Launching ULTRA MINER (CPU multiprocessing)...")
        print()
        
        # Update config if wallet provided
        if args.wallet:
            import ultra_miner
            ultra_miner.WALLET_ADDRESS = args.wallet
            ultra_miner.WORKER_NAME = args.worker
            ultra_miner.POOL_HOST = args.pool
            ultra_miner.POOL_PORT = args.port
            ultra_miner.NUM_WORKERS = args.threads
        
        os.system(f"{sys.executable} ultra_miner.py")


if __name__ == "__main__":
    main()