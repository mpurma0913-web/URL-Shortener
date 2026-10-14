.PHONY: install init run test check security contracts smoke
install:
	python -m pip install -r requirements-dev.txt
	python -m pip install --no-deps -e .
init:
	python scripts/init_env.py
run:
	python -m shortener
test:
	python -m pytest --cov=shortener --cov-branch --cov-report=term-missing
check:
	python scripts/quality.py --local
security:
	python scripts/security_checks.py
contracts:
	python scripts/export_contracts.py --check
smoke:
	python scripts/smoke.py
