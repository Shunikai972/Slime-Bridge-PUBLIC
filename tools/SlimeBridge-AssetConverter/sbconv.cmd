@echo off
rem SlimeBridge-AssetConverter launcher: runs the CLI with the project's virtual environment.
"%~dp0.venv\Scripts\python.exe" -m sbconv.cli %*
