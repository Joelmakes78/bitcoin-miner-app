import argparse
import time

class BitcoinMiner:
    def __init__(self, mode):
        self.mode = mode
        self.is_running = False

    def start(self):
        self.is_running = True
        print(f"{self.mode.capitalize()} mode: Miner started.")
        while self.is_running:
            # Simulate mining work
            print("Mining...")
            time.sleep(5)  # Simulate time taken for mining

    def stop(self):
        self.is_running = False
        print(f"{self.mode.capitalize()} mode: Miner stopped.")

def interactive_mode():
    miner = BitcoinMiner(mode='interactive')
    miner.start()  
    input("Press Enter to stop mining...")
    miner.stop()


def daemon_mode():
    miner = BitcoinMiner(mode='daemon')
    miner.start()  
    # In an actual daemon,  you would add the logic to run this as a service or background task

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Bitcoin Miner CLI')
    parser.add_argument('--mode', choices=['interactive', 'daemon'], required=True, help='Choose the mode of operation.')
    args = parser.parse_args()

    if args.mode == 'interactive':
        interactive_mode()
    elif args.mode == 'daemon':
        daemon_mode()