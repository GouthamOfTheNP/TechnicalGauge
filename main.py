import sys
import os
import traceback
import matplotlib
import pandas as pd
import numpy as np
import pandas_ta as ta
import yfinance as yf
import matplotlib.dates as mdates
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QPushButton, QVBoxLayout, QHBoxLayout,
                               QWidget, QLabel, QLineEdit, QComboBox, QTextEdit, QGroupBox,
                               QSpinBox, QMessageBox,QFrame, QGridLayout, QCompleter, QCheckBox)
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont, QColor, QPalette
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

os.environ["QT_API"] = "PySide6"
matplotlib.use("QtAgg")

DEFAULT_STRONG_BUY = 17
DEFAULT_BUY = 10
DEFAULT_SELL = -10
DEFAULT_STRONG_SELL = -17

POPULAR_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "AMD", "NFLX", "COIN",
    "BTC-USD", "ETH-USD", "SOL-USD", "SPY", "QQQ", "IWM", "TSM", "AVGO", "ORCL", 
    "CRM", "INTC", "JPM", "V", "MA", "WMT", "DIS", "PYPL", "SQ", "MSTR", "PLTR"
]


class DataFetchThread(QThread):
    finished = Signal(object, str, str)
    
    def __init__(self, ticker, period, interval):
        super().__init__()
        self.ticker = ticker.upper().strip()
        self.period = period
        self.interval = interval
        
    def run(self):
        try:
            safe_interval = self.interval.replace("hr", "h")
            
            df = yf.download(
                self.ticker, 
                period=self.period, 
                interval=safe_interval, 
                progress=False, 
                multi_level_index=False
            )
            
            if df is None or df.empty:
                self.finished.emit(None, self.ticker, "No data found. Check ticker or internet connection.")
                return
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            if len(df) < 20:
                self.finished.emit(None, self.ticker, "Insufficient data history for analysis.")
                return
            
            self.finished.emit(df, self.ticker, "")
        except Exception as e:
            self.finished.emit(None, self.ticker, str(e))


class TradingDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Technical Gauge Dashboard")
        self.resize(1600, 1000)
        
        self.current_df = None
        self.fetch_thread = None
        self.countdown_val = 60
        
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.run_analysis_silent)
        
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_countdown)
        
        self.setup_ui()
        self.apply_theme()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        sidebar = QWidget()
        sidebar.setFixedWidth(420)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 5, 0)

        mkt_group = QGroupBox("1. Market Selection")
        mkt_grid = QGridLayout(mkt_group)
        
        self.ticker_input = QLineEdit()
        self.ticker_input.setPlaceholderText("Symbol (e.g. NVDA)...")
        self.ticker_input.setFont(QFont("Arial", 11, QFont.Bold))
        completer = QCompleter(POPULAR_TICKERS)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.ticker_input.setCompleter(completer)
        self.ticker_input.returnPressed.connect(self.run_analysis)

        self.interval_box = QComboBox()
        self.interval_box.addItems(["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo"])
        self.interval_box.setCurrentText("1d")
        self.interval_box.currentTextChanged.connect(self.validate_period_compatibility)
        
        self.period_box = QComboBox()
        self.period_box.addItems(["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"])
        self.period_box.setCurrentText("1y")

        mkt_grid.addWidget(QLabel("Ticker:"), 0, 0)
        mkt_grid.addWidget(self.ticker_input, 0, 1, 1, 2)
        mkt_grid.addWidget(QLabel("Interval:"), 1, 0)
        mkt_grid.addWidget(self.interval_box, 1, 1)
        mkt_grid.addWidget(self.period_box, 1, 2)
        side_layout.addWidget(mkt_group)

        strat_group = QGroupBox("2. Signal Sensitivity")
        strat_grid = QGridLayout(strat_group)
        
        self.spin_s_buy = self.create_spin(DEFAULT_STRONG_BUY, 10, 100)
        self.spin_buy = self.create_spin(DEFAULT_BUY, 0, 50)
        self.spin_sell = self.create_spin(DEFAULT_SELL, -50, 0)
        self.spin_s_sell = self.create_spin(DEFAULT_STRONG_SELL, -100, -10)

        for spin in [self.spin_s_buy, self.spin_buy, self.spin_sell, self.spin_s_sell]:
            spin.valueChanged.connect(self.update_signal_display)

        strat_grid.addWidget(QLabel("Strong Buy (>):"), 0, 0)
        strat_grid.addWidget(self.spin_s_buy, 0, 1)
        strat_grid.addWidget(QLabel("Buy (>):"), 1, 0)
        strat_grid.addWidget(self.spin_buy, 1, 1)
        strat_grid.addWidget(QLabel("Sell (<):"), 2, 0)
        strat_grid.addWidget(self.spin_sell, 2, 1)
        strat_grid.addWidget(QLabel("Strong Sell (<):"), 3, 0)
        strat_grid.addWidget(self.spin_s_sell, 3, 1)
        side_layout.addWidget(strat_group)

        exec_group = QGroupBox("3. Execution")
        exec_layout = QVBoxLayout(exec_group)
        
        refresh_layout = QHBoxLayout()
        self.auto_refresh_cb = QCheckBox("Auto-Refresh")
        self.auto_refresh_cb.toggled.connect(self.toggle_autorefresh)
        self.countdown_lbl = QLabel("(OFF)")
        self.countdown_lbl.setStyleSheet("color: #94a3b8;")
        refresh_layout.addWidget(self.auto_refresh_cb)
        refresh_layout.addWidget(self.countdown_lbl)
        
        self.analyze_btn = QPushButton("RUN ANALYSIS")
        self.analyze_btn.setMinimumHeight(40)
        self.analyze_btn.clicked.connect(self.run_analysis)

        exec_layout.addLayout(refresh_layout)
        exec_layout.addWidget(self.analyze_btn)
        side_layout.addWidget(exec_group)

        self.sig_frame = QFrame()
        self.sig_frame.setFrameShape(QFrame.StyledPanel)
        self.sig_frame.setMinimumHeight(100)
        sig_layout = QVBoxLayout(self.sig_frame)
        self.sig_lbl = QLabel("READY")
        self.sig_lbl.setFont(QFont("Arial", 28, QFont.Bold))
        self.sig_lbl.setAlignment(Qt.AlignCenter)
        self.score_lbl = QLabel("Score: --")
        self.score_lbl.setAlignment(Qt.AlignCenter)
        sig_layout.addWidget(self.sig_lbl)
        sig_layout.addWidget(self.score_lbl)
        side_layout.addWidget(self.sig_frame)

        matrix_group = QGroupBox("Technical Matrix")
        self.matrix_layout = QGridLayout(matrix_group)
        self.indicators_labels = {}
        
        indicators = [
            "Trend (EMA/SMA)", "Trend Strength (ADX)", "Momentum (RSI)", 
            "MACD", "Stoch RSI", "Williams %R", 
            "Volatility (BB)", "Volume (OBV)", "Parabolic SAR"
        ]
        
        for i, name in enumerate(indicators):
            lbl = QLabel(name)
            val = QLabel("--")
            val.setAlignment(Qt.AlignRight)
            val.setStyleSheet("color: #64748b")
            self.matrix_layout.addWidget(lbl, i, 0)
            self.matrix_layout.addWidget(val, i, 1)
            self.indicators_labels[name] = val
            
        side_layout.addWidget(matrix_group)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(150)
        self.log_box.setPlaceholderText("Analysis details will appear here...")
        side_layout.addWidget(self.log_box)

        main_layout.addWidget(sidebar)

        chart_container = QWidget()
        chart_layout = QVBoxLayout(chart_container)
        
        self.figure = Figure(figsize=(10, 8), dpi=100, facecolor="#0f172a")
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor("#0f172a")
        
        chart_layout.addWidget(self.canvas)
        main_layout.addWidget(chart_container, stretch=1)

    def create_spin(self, val, min_val, max_val):
        spin = QSpinBox()
        spin.setRange(min_val, max_val)
        spin.setValue(val)
        return spin

    def apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #0f172a; color: #f8fafc; font-family: "Segoe UI", sans-serif; }
            QGroupBox { border: 1px solid #334155; border-radius: 6px; margin-top: 10px; padding-top: 10px; font-weight: bold; color: #cbd5e1; }
            QLineEdit, QComboBox, QSpinBox { background-color: #1e293b; border: 1px solid #475569; padding: 5px; border-radius: 4px; color: white; }
            QPushButton { background-color: #3b82f6; color: white; border: none; padding: 8px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:disabled { background-color: #334155; color: #94a3b8; }
            QFrame { background-color: #1e293b; border-radius: 8px; border: 1px solid #334155; }
            QTextEdit { background-color: #1e293b; border: 1px solid #334155; font-family: Consolas; font-size: 11px; }
        """)

    def validate_period_compatibility(self):
        interval = self.interval_box.currentText()
        period = self.period_box.currentText()
        
        if interval == "1m":
            if period not in ["1d", "5d"]: 
                self.period_box.setCurrentText("1d")
        elif interval in ["2m", "5m", "15m", "30m", "90m"]:
            if period not in ["1d", "5d", "1mo"]: 
                self.period_box.setCurrentText("1mo")
        elif interval == "60m" or interval == "1h":
            if period in ["5y", "max"]: 
                self.period_box.setCurrentText("2y")

    def toggle_autorefresh(self):
        if self.auto_refresh_cb.isChecked():
            self.countdown_val = 60
            self.refresh_timer.start(60000)
            self.ui_timer.start(1000)
            self.update_countdown()
            self.run_analysis()
        else:
            self.refresh_timer.stop()
            self.ui_timer.stop()
            self.countdown_lbl.setText("(OFF)")

    def update_countdown(self):
        self.countdown_val -= 1
        if self.countdown_val < 0: self.countdown_val = 60
        self.countdown_lbl.setText(f"Next: {self.countdown_val}s")

    def run_analysis_silent(self):
        self.countdown_val = 60
        self.run_analysis(silent=True)

    def run_analysis(self, silent=False):
        ticker = self.ticker_input.text().strip()
        if not ticker: return
        
        if self.fetch_thread and self.fetch_thread.isRunning():
            return

        if not silent:
            self.analyze_btn.setEnabled(False)
            self.analyze_btn.setText("FETCHING...")
        
        period = self.period_box.currentText()
        interval = self.interval_box.currentText()
        
        self.fetch_thread = DataFetchThread(ticker, period, interval)
        self.fetch_thread.finished.connect(self.on_data_ready)
        self.fetch_thread.start()

    def on_data_ready(self, df, ticker, error):
        self.analyze_btn.setEnabled(True)
        self.analyze_btn.setText("RUN ANALYSIS")
        
        if error:
            self.log_box.setText(f"Error: {error}")
            if not self.auto_refresh_cb.isChecked():
                QMessageBox.warning(self, "Data Error", error)
            return

        try:
            self.current_df = self.calculate_indicators(df)
            self.update_signal_display()
            self.update_chart(ticker)
        except Exception as e:
            traceback.print_exc()
            self.log_box.setText(f"Math Error: {str(e)}")

    def calculate_indicators(self, df):
        df = df.copy()
        
        df.ta.ema(length=20, append=True, col_names=("EMA20",))
        df.ta.sma(length=200, append=True, col_names=("SMA200",))
        
        df.ta.adx(length=14, append=True)
        cols = df.columns
        try:
            df["ADX"] = df[[c for c in cols if c.startswith("ADX_")][0]]
            df["DMP"] = df[[c for c in cols if c.startswith("DMP_")][0]]
            df["DMN"] = df[[c for c in cols if c.startswith("DMN_")][0]]
        except: pass

        df.ta.rsi(length=14, append=True, col_names=("RSI",))
        macd = df.ta.macd()
        if macd is not None:
            df["MACD"] = macd.iloc[:, 0]
            df["MACD_S"] = macd.iloc[:, 2]
        
        df.ta.stochrsi(append=True, col_names=("STOCH_K", "STOCH_D"))
        df.ta.willr(append=True, col_names=("WILLR",))
        
        bb = df.ta.bbands(length=20, std=2)
        if bb is not None:
            df["BBL"] = bb.iloc[:, 0]
            df["BBU"] = bb.iloc[:, 2]
        
        df.ta.obv(append=True, col_names=("OBV",))
        psar = df.ta.psar()
        if psar is not None:
            df["PSAR"] = psar.iloc[:, 0].fillna(psar.iloc[:, 1])

        return df

    def update_signal_display(self):
        if self.current_df is None or len(self.current_df) < 5: return
        df = self.current_df
        curr = df.iloc[-1]
        score = 0
        reasons = []

        def set_status(key, text, color="white"):
            self.indicators_labels[key].setText(text)
            self.indicators_labels[key].setStyleSheet(f"color: {color}; font-weight: bold;")

        if curr["Close"] > curr.get("EMA20", curr["Close"]):
            score += 10
            set_status("Trend (EMA/SMA)", "BULLISH", "#4ade80")
        else:
            score -= 10
            set_status("Trend (EMA/SMA)", "BEARISH", "#f87171")

        if "ADX" in curr and curr["ADX"] > 25:
            trend_str = "STRONG"
            if curr.get("DMP", 0) > curr.get("DMN", 0): score += 5
            else: score -= 5
        else:
            trend_str = "WEAK"
        set_status("Trend Strength (ADX)", trend_str)

        rsi = curr.get("RSI", 50)
        if rsi < 30:
            score += 15
            reasons.append(f"RSI Oversold ({rsi:.1f})")
            set_status("Momentum (RSI)", "OVERSOLD", "#4ade80")
        elif rsi > 70:
            score -= 15
            reasons.append(f"RSI Overbought ({rsi:.1f})")
            set_status("Momentum (RSI)", "OVERBOUGHT", "#f87171")
        else:
            set_status("Momentum (RSI)", "NEUTRAL", "#94a3b8")

        if curr.get("MACD", 0) > curr.get("MACD_S", 0):
            score += 5
            set_status("MACD", "BULLISH", "#4ade80")
        else:
            score -= 5
            set_status("MACD", "BEARISH", "#f87171")

        close = curr["Close"]
        if close < curr.get("BBL", close):
            score += 10
            reasons.append("Price below Lower Band")
            set_status("Volatility (BB)", "LOW BREAK", "#4ade80")
        elif close > curr.get("BBU", close):
            score -= 10
            reasons.append("Price above Upper Band")
            set_status("Volatility (BB)", "HIGH BREAK", "#f87171")
        else:
            set_status("Volatility (BB)", "INSIDE", "#94a3b8")

        stoch_k = curr.get("STOCH_K", 50)
        if stoch_k < 20: 
            score += 5
            set_status("Stoch RSI", "OVERSOLD", "#4ade80")
        elif stoch_k > 80: 
            score -= 5
            set_status("Stoch RSI", "OVERBOUGHT", "#f87171")
        else:
            set_status("Stoch RSI", "NEUTRAL", "#94a3b8")

        wr = curr.get("WILLR", -50)
        set_status("Williams %R", f"{wr:.1f}")

        if "OBV" in df.columns:
            obv_ma = df["OBV"].rolling(5).mean()
            obv_slope = obv_ma.iloc[-1] - obv_ma.iloc[-2]
            if obv_slope > 0: set_status("Volume (OBV)", "ACCUMULATION", "#4ade80")
            else: set_status("Volume (OBV)", "DISTRIBUTION", "#f87171")

        if "PSAR" in curr:
            if close > curr["PSAR"]: set_status("Parabolic SAR", "BULLISH", "#4ade80")
            else: set_status("Parabolic SAR", "BEARISH", "#f87171")

        s_buy, buy, sell, s_sell = [spin.value() for spin in 
                                    [self.spin_s_buy, self.spin_buy, self.spin_sell, self.spin_s_sell]]
        if score >= s_buy: sig, col = "STRONG BUY", "#22c55e"
        elif score >= buy: sig, col = "BUY", "#3b82f6"
        elif score <= s_sell: sig, col = "STRONG SELL", "#ef4444"
        elif score <= sell: sig, col = "SELL", "#f472b6"
        else: sig, col = "HOLD", "#94a3b8"

        self.sig_lbl.setText(sig)
        self.sig_lbl.setStyleSheet(f"color: {col};")
        self.score_lbl.setText(f"Total Score: {score}")

        log_txt = f"[{datetime.now().strftime('%H:%M:%S')}] {self.ticker_input.text().upper()} Analysis\n"
        if reasons:
            log_txt += "Key Drivers:\n" + "\n".join([f"- {r}" for r in reasons])
        else:
            log_txt += "Market following primary trend. No extremes detected."
        self.log_box.setText(log_txt)

    def update_chart(self, ticker):
        self.ax.clear()
        df = self.current_df
        window = 100
        plot_df = df.iloc[-window:] if len(df) > window else df

        self.ax.plot(plot_df.index, plot_df["Close"], color="#3b82f6", label="Price", linewidth=2)
        if "EMA20" in plot_df: self.ax.plot(plot_df.index, plot_df["EMA20"], color="#f59e0b", linestyle="--", label="EMA 20", alpha=0.8)
        if "BBU" in plot_df and "BBL" in plot_df: self.ax.fill_between(plot_df.index, plot_df["BBU"], plot_df["BBL"], color="#3b82f6", alpha=0.1)
        if "PSAR" in plot_df: self.ax.scatter(plot_df.index, plot_df["PSAR"], color="#cbd5e1", s=3, label="SAR")

        self.ax.set_title(f"{ticker} ({self.interval_box.currentText()})", color="white", fontweight="bold", pad=10)
        self.ax.grid(True, color="#334155", alpha=0.3, linestyle="--")
        self.ax.tick_params(axis="x", colors="#cbd5e1", rotation=25)
        self.ax.tick_params(axis="y", colors="#cbd5e1")
        for spine in self.ax.spines.values(): spine.set_color("#475569")

        interval_text = self.interval_box.currentText()
        if "m" in interval_text or "h" in interval_text:
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        else:
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))

        self.canvas.draw()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(15, 23, 42))
    palette.setColor(QPalette.WindowText, Qt.white)
    app.setPalette(palette)
    
    window = TradingDashboard()
    window.show()
    sys.exit(app.exec())
