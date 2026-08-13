.PHONY: docker-up docker-down docker-logs docker-ps test lint format evaluate compile compose-config clean

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=200

docker-ps:
	docker compose ps

compose-config:
	docker compose config

test:
	pytest -q

lint:
	ruff check .

format:
	ruff format .

evaluate:
	python scripts/evaluate.py

compile:
	python -m compileall -q app ui tests scripts

clean:
	docker compose down -v --remove-orphans

smoke:
	python scripts/smoke_test.py
