#!/usr/bin/env python3
"""
Bitcoin Miner GUI - Modern PyQt6 Interface
==========================================
A beautiful, modern GUI for the Bitcoin miner with real-time stats.

Usage: python gui.py
"""

import sys
import os
import time
import threading
from datetime import datetime

# PyQt6 imports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QGridLayout,
    QProgressBar, QFrame, QComboBox, QSpinBox, QMessageBox,
    QStatusBar, QTabWidget, QTextEdit, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QIcon, QColor, QPalette, QGradient, QPainter

# Import miner
try:
    from ultra_miner import UltraMiner
    HAS_MINER = True
except ImportError:
    HAS_MINER = False


# =============================================================================
# STYLE SHEET - Modern Dark Theme
# =============================================================================
STYLESHEET = """
QMainWindow {
    background-color: #1a1a2e;
}

QWidget {
    background-color: #1a1a2e;
    color: #eaeaea;
    font-family: 'Segoe UI', Arial, sans-serif;
}

QGroupBox {
    border: 2px solid #16213e;
    border-radius: 10px;
    margin-top: 20px;
    padding-top: 10px;
    font-weight: bold;
    font-size: 14px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 20px;
    padding: 0 10px;
    color: #e94560;
}

QLabel {
    font-size: 13px;
    padding: 5px;
}

QLineEdit {
    background-color: #16213e;
    border: 2px solid #0f3460;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    color: #eaeaea;
}

QLineEdit:focus {
    border-color: #e94560;
}

QLineEdit:disabled {
    background-color: #0d1b2a;
    color: #888888;
}

QPushButton {
    background-color: #e94560;
    border: none;
    border-radius: 8px;
    padding: 12px 25px;
    font-size: 14px;
    font-weight: bold;
    color: white;
}

QPushButton:hover {
    background-color: #ff6b6b;
}

QPushButton:pressed {
    background-color: #c73e54;
}

QPushButton:disabled {
    background-color: #3d3d5c;
    color: #888888;
}

QPushButton#startButton {
    background-color: #00b894;
}

QPushButton#startButton:hover {
    background-color: #00d9a5;
}

QPushButton#startButton:disabled {
    background-color: #3d3d5c;
}

QPushButton#stopButton {
    background-color: #e94560;
}

QComboBox {
    background-color: #16213e;
    border: 2px solid #0f3460;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    color: #eaeaea;
}

QComboBox::drop-down {
    border: none;
    width: 30px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 8px solid #e94560;
    margin-right: 10px;
}

QSpinBox {
    background-color: #16213e;
    border: 2px solid #0f3460;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    color: #eaeaea;
}

QTabWidget::pane {
    border: 2px solid #16213e;
    border-radius: 10px;
    background-color: #1a1a2e;
}

QTabBar::tab {
    background-color: #16213e;
    border: none;
    padding: 10px 25px;
    margin-right: 5px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-size: 13px;
}

QTabBar::tab:selected {
    background-color: #e94560;
    color: white;
}

QTabBar::tab:hover {
    background-color: #0f3460;
}

QTextEdit {
    background-color: #16213e;
    border: 2px solid #0f3460;
    border-radius: 8px;
    padding: 10px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    color: #00ff88;
}

QStatusBar {
    background-color: #16213e;
    border-top: 1px solid #0f3460;
    padding: 5px;
}

QScrollBar:vertical {
    background-color: #16213e;
    width: 12px;
    border-radius: 6px;
}

QScrollBar::handle:vertical {
    background-color: #0f3460;
    border-radius: 6px;
    min-height: 20px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""


# =============================================================================
# STATS CARD WIDGET
# =============================================================================
class StatsCard(QFrame):
    """A beautiful card widget for displaying stats."""
    
    def __init__(self, title: str, value: str = "0", icon: str = "⚡", parent=None):
        super().__init__(parent)
        self.setObjectName("statsCard")
        self.setStyleSheet("""
            QFrame#statsCard {
                background-color: #16213e;
                border-radius: 15px;
                padding: 15px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        
        # Title with icon
        title_label = QLabel(f"{icon} {title}")
        title_label.setStyleSheet("font-size: 12px; color: #888888; border: none;")
        layout.addWidget(title_label)
        
        # Value
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("""
            font-size: 28px; 
            font-weight: bold; 
            color: #00ff88;
            border: none;
        """)
        layout.addWidget(self.value_label)
        
    def set_value(self, value: str):
        self.value_label.setText(value)


# =============================================================================
# MINER THREAD
# =============================================================================
class MinerThread(QThread):
    """Background thread for mining operations."""
    
    stats_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    
    def __init__(self, wallet: str, worker: str, pool_host: str, pool_port: int,
                 threads: int, password: str = "x"):
        super().__init__()
        self.wallet = wallet
        self.worker = worker
        self.pool_host = pool_host
        self.pool_port = pool_port
        self.threads = threads
        self.password = password
        self.miner = None
        self.running = False
        
    def run(self):
        """Start mining in background."""
        if not HAS_MINER:
            self.log_signal.emit("❌ Miner module not found!")
            return
            
        self.running = True
        self.log_signal.emit(f"🚀 Starting miner...")
        self.log_signal.emit(f"📋 Wallet: {self.wallet}")
        self.log_signal.emit(f"🔗 Pool: {self.pool_host}:{self.pool_port}")
        self.log_signal.emit(f"🧵 Threads: {self.threads}")
        
        try:
            self.miner = UltraMiner(
                wallet=self.wallet,
                worker=self.worker,
                pool_host=self.pool_host,
                pool_port=self.pool_port,
                num_workers=self.threads,
                password=self.password
            )
            
            # Set callbacks
            self.miner.on_hashrate_update = lambda rate: self.stats_signal.emit({
                'hashrate': rate,
                'hashes': self.miner.hashes_total if self.miner else 0,
                'shares': self.miner.shares_found if self.miner else 0
            })
            
            if self.miner.start():
                self.status_signal.emit("⛏️ Mining...")
                self.log_signal.emit("✅ Connected to pool!")
                self.log_signal.emit("⛏️ Mining started!")
                
                # Keep running
                while self.running:
                    time.sleep(1)
                    if self.miner:
                        stats = self.miner.get_stats()
                        self.stats_signal.emit(stats)
            else:
                self.log_signal.emit("❌ Failed to start miner!")
                self.status_signal.emit("❌ Failed")
                
        except Exception as e:
            self.log_signal.emit(f"❌ Error: {str(e)}")
            self.status_signal.emit(f"❌ Error")
            
    def stop(self):
        """Stop mining."""
        self.running = False
        if self.miner:
            self.miner.stop()
        self.log_signal.emit("🛑 Mining stopped")
        self.status_signal.emit("🛑 Stopped")


# =============================================================================
# MAIN WINDOW
# =============================================================================
class BitcoinMinerGUI(QMainWindow):
    """Main GUI window for the Bitcoin miner."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚡ Bitcoin Miner - WARP Edition")
        self.setMinimumSize(900, 700)
        self.resize(1000, 750)
        
        self.miner_thread = None
        self.start_time = None
        
        # Stats tracking
        self.total_hashes = 0
        self.shares_found = 0
        self.current_hashrate = 0
        
        self._setup_ui()
        self._setup_timer()
        
    def _setup_ui(self):
        """Set up the user interface."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Header
        header = QLabel("⚡ WARP BITCOIN MINER")
        header.setStyleSheet("""
            font-size: 28px;
            font-weight: bold;
            color: #e94560;
            padding: 10px;
        """)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(header)
        
        # Tab widget
        tabs = QTabWidget()
        main_layout.addWidget(tabs)
        
        # Configuration Tab
        config_tab = QWidget()
        config_layout = QVBoxLayout(config_tab)
        tabs.addTab(config_tab, "⚙️ Configuration")
        
        # Pool Settings Group
        pool_group = QGroupBox("🔗 Pool Settings")
        pool_layout = QGridLayout(pool_group)
        
        pool_layout.addWidget(QLabel("Pool:"), 0, 0)
        self.pool_combo = QComboBox()
        self.pool_combo.addItems([
            "F2Pool (btc.f2pool.com:3333)",
            "AntPool (stratum.antpool.com:3333)",
            "ViaBTC (btc.viabtc.com:3333)",
            "Solo CKPool (solo.ckpool.org:3333)",
            "Custom Pool..."
        ])
        self.pool_combo.currentIndexChanged.connect(self._on_pool_changed)
        pool_layout.addWidget(self.pool_combo, 0, 1, 1, 2)
        
        pool_layout.addWidget(QLabel("Custom Host:"), 1, 0)
        self.custom_host_input = QLineEdit()
        self.custom_host_input.setPlaceholderText("e.g., btc.f2pool.com")
        self.custom_host_input.setEnabled(False)
        pool_layout.addWidget(self.custom_host_input, 1, 1)
        
        pool_layout.addWidget(QLabel("Port:"), 1, 2)
        self.port_input = QLineEdit("3333")
        self.port_input.setPlaceholderText("3333")
        self.port_input.setMaximumWidth(100)
        pool_layout.addWidget(self.port_input, 1, 3)
        
        config_layout.addWidget(pool_group)
        
        # Wallet Settings Group
        wallet_group = QGroupBox("💼 Wallet Settings")
        wallet_layout = QGridLayout(wallet_group)
        
        wallet_layout.addWidget(QLabel("Wallet Address:"), 0, 0)
        self.wallet_input = QLineEdit()
        self.wallet_input.setPlaceholderText("Enter your Bitcoin wallet address")
        self.wallet_input.setText("1HwQuxoGiUciUKE99NDJifNBpRp63er4Nw")
        wallet_layout.addWidget(self.wallet_input, 0, 1, 1, 3)
        
        wallet_layout.addWidget(QLabel("Worker Name:"), 1, 0)
        self.worker_input = QLineEdit("Worker1")
        self.worker_input.setPlaceholderText("Worker name (e.g., Worker1)")
        wallet_layout.addWidget(self.worker_input, 1, 1)
        
        wallet_layout.addWidget(QLabel("Password:"), 1, 2)
        self.password_input = QLineEdit("x")
        self.password_input.setPlaceholderText("Pool password (usually 'x')")
        wallet_layout.addWidget(self.password_input, 1, 3)
        
        config_layout.addWidget(wallet_group)
        
        # Mining Settings Group
        mining_group = QGroupBox("⛏️ Mining Settings")
        mining_layout = QGridLayout(mining_group)
        
        mining_layout.addWidget(QLabel("CPU Threads:"), 0, 0)
        self.threads_spin = QSpinBox()
        self.threads_spin.setMinimum(1)
        self.threads_spin.setMaximum(64)
        import multiprocessing
        self.threads_spin.setValue(multiprocessing.cpu_count())
        mining_layout.addWidget(self.threads_spin, 0, 1)
        
        mining_layout.addWidget(QLabel(f"(System has {multiprocessing.cpu_count()} cores)"), 0, 2, 1, 2)
        
        config_layout.addWidget(mining_group)
        
        # Stats Tab
        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        tabs.addTab(stats_tab, "📊 Statistics")
        
        # Stats Cards
        cards_layout = QHBoxLayout()
        
        self.hashrate_card = StatsCard("Hashrate", "0 H/s", "⚡")
        cards_layout.addWidget(self.hashrate_card)
        
        self.hashes_card = StatsCard("Total Hashes", "0", "🔢")
        cards_layout.addWidget(self.hashes_card)
        
        self.shares_card = StatsCard("Shares Found", "0", "🎯")
        cards_layout.addWidget(self.shares_card)
        
        self.uptime_card = StatsCard("Uptime", "0s", "⏱️")
        cards_layout.addWidget(self.uptime_card)
        
        stats_layout.addLayout(cards_layout)
        
        # Log output
        log_group = QGroupBox("📝 Mining Log")
        log_layout = QVBoxLayout(log_group)
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Mining logs will appear here...")
        log_layout.addWidget(self.log_output)
        
        stats_layout.addWidget(log_group)
        
        # Control Buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(15)
        
        self.start_button = QPushButton("▶️ START MINING")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self._start_mining)
        buttons_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("⏹️ STOP MINING")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self._stop_mining)
        self.stop_button.setEnabled(False)
        buttons_layout.addWidget(self.stop_button)
        
        main_layout.addLayout(buttons_layout)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready to mine 🚀")
        
    def _setup_timer(self):
        """Set up the update timer."""
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_stats)
        self.update_timer.start(1000)  # Update every second
        
    def _on_pool_changed(self, index):
        """Handle pool selection change."""
        if index == 4:  # Custom pool
            self.custom_host_input.setEnabled(True)
        else:
            self.custom_host_input.setEnabled(False)
            
    def _get_pool_info(self) -> tuple:
        """Get selected pool host and port."""
        pools = {
            0: ("btc.f2pool.com", 3333),
            1: ("stratum.antpool.com", 3333),
            2: ("btc.viabtc.com", 3333),
            3: ("solo.ckpool.org", 3333),
        }
        
        index = self.pool_combo.currentIndex()
        if index == 4:  # Custom
            host = self.custom_host_input.text().strip()
            port = int(self.port_input.text().strip() or "3333")
            return (host, port)
        else:
            return pools.get(index, ("btc.f2pool.com", 3333))
            
    def _start_mining(self):
        """Start the mining process."""
        wallet = self.wallet_input.text().strip()
        if not wallet:
            QMessageBox.warning(self, "Warning", "Please enter your wallet address!")
            return
            
        pool_host, pool_port = self._get_pool_info()
        worker = self.worker_input.text().strip() or "Worker1"
        threads = self.threads_spin.value()
        password = self.password_input.text().strip() or "x"
        
        self.start_time = time.time()
        self.total_hashes = 0
        self.shares_found = 0
        
        # Create and start miner thread
        self.miner_thread = MinerThread(
            wallet=wallet,
            worker=worker,
            pool_host=pool_host,
            pool_port=pool_port,
            threads=threads,
            password=password
        )
        
        # Connect signals
        self.miner_thread.stats_signal.connect(self._on_stats_update)
        self.miner_thread.log_signal.connect(self._on_log)
        self.miner_thread.status_signal.connect(self._on_status)
        
        self.miner_thread.start()
        
        # Update UI
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_inputs_enabled(False)
        
    def _stop_mining(self):
        """Stop the mining process."""
        if self.miner_thread:
            self.miner_thread.stop()
            self.miner_thread = None
            
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_inputs_enabled(True)
        
    def _set_inputs_enabled(self, enabled: bool):
        """Enable/disable input fields."""
        self.wallet_input.setEnabled(enabled)
        self.worker_input.setEnabled(enabled)
        self.pool_combo.setEnabled(enabled)
        self.threads_spin.setEnabled(enabled)
        self.password_input.setEnabled(enabled)
        
    def _on_stats_update(self, stats: dict):
        """Handle stats update from miner."""
        self.current_hashrate = stats.get('hashrate', 0)
        self.total_hashes = stats.get('hashes', 0)
        self.shares_found = stats.get('shares', 0)
        
    def _on_log(self, message: str):
        """Handle log message from miner."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {message}")
        
    def _on_status(self, status: str):
        """Handle status update from miner."""
        self.status_bar.showMessage(status)
        
    def _update_stats(self):
        """Update the stats display."""
        # Format hashrate
        rate = self.current_hashrate
        if rate >= 1e9:
            rate_str = f"{rate/1e9:.2f} GH/s"
        elif rate >= 1e6:
            rate_str = f"{rate/1e6:.2f} MH/s"
        elif rate >= 1e3:
            rate_str = f"{rate/1e3:.2f} KH/s"
        else:
            rate_str = f"{rate:.1f} H/s"
            
        self.hashrate_card.set_value(rate_str)
        self.hashes_card.set_value(f"{self.total_hashes:,}")
        self.shares_card.set_value(str(self.shares_found))
        
        # Update uptime
        if self.start_time:
            uptime = int(time.time() - self.start_time)
            hours = uptime // 3600
            minutes = (uptime % 3600) // 60
            seconds = uptime % 60
            if hours > 0:
                uptime_str = f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                uptime_str = f"{minutes}m {seconds}s"
            else:
                uptime_str = f"{seconds}s"
            self.uptime_card.set_value(uptime_str)
            
    def closeEvent(self, event):
        """Handle window close event."""
        if self.miner_thread:
            self._stop_mining()
        event.accept()


# =============================================================================
# MAIN
# =============================================================================
def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    
    window = BitcoinMinerGUI()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()