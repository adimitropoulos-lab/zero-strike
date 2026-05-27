.PHONY: setup run run-bg stop logs test docker-up docker-down docker-logs

PYTHON ?= python3
PID_FILE := .zero-strike.pid
LOG_FILE := zero-strike.log

setup:
	$(PYTHON) -m pip install -e .
	@test -f .env || cp .env.example .env
	@echo "Edit .env to set ANTHROPIC_API_KEY, then: make run"

run:
	zero-strike run

run-bg:
	@nohup zero-strike run >> $(LOG_FILE) 2>&1 & echo $$! > $(PID_FILE)
	@echo "started pid $$(cat $(PID_FILE)) — tail -f $(LOG_FILE)"

stop:
	@if [ -f $(PID_FILE) ]; then kill $$(cat $(PID_FILE)) && rm $(PID_FILE) && echo "stopped"; else echo "no pid file"; fi

logs:
	@tail -f $(LOG_FILE)

test:
	$(PYTHON) -m pytest -q

docker-up:
	docker compose up -d --build
	docker compose logs -f --tail=50

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=200
