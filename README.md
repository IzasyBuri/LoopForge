# LoopForge

Phase 0 desktop application foundation.

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m loopforge
pytest
ruff check .
mypy
```
