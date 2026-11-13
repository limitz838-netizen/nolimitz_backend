"""Refresh gold price data (COMEX gold futures GC=F via Yahoo Finance).  pip install yfinance"""
import yfinance as yf

yf.download("GC=F", start="2000-01-01", interval="1d", auto_adjust=False, progress=False).to_csv("data/gold_daily.csv")
yf.download("GC=F", period="730d", interval="1h", progress=False).to_csv("data/gold_1h.csv")
print("saved data/gold_daily.csv and data/gold_1h.csv")
