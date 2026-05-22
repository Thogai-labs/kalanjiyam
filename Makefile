# Environment. Valid values are: local, staging, prod
KALANJIYAM_DEPLOYMENT_ENV=local
KALANJIYAM_HOST_IP=0.0.0.0
KALANJIYAM_HOST_PORT=5000

# Control the verbosity of messages using a flag
ifdef mode
	ifeq ("$(origin mode)", "command line")
		BUILD_MODE = $(mode)
	endif
else
	BUILD_MODE = default
endif

ifdef ($(BUILD_MODE),dev)
	IO_REDIRECT = 
	DOCKER_VERBOSITY = 
	DOCKER_LOG_LEVEL = 
	DOCKER_DETACH = 
else ifeq ($(BUILD_MODE),quiet)
	IO_REDIRECT = &> /dev/null
	DOCKER_VERBOSITY = -qq
	DOCKER_LOG_LEVEL = --log-level ERROR
	DOCKER_DETACH = --detach
else ifeq ($(BUILD_MODE),default)
	IO_REDIRECT = 
	DOCKER_VERBOSITY = 
	DOCKER_LOG_LEVEsL = 
	DOCKER_DETACH = --detach
endif

# Needed because we have folders called "docs" and "test" that confuse `make`.
.PHONY: docs test py-venv-check clean

.EXPORT_ALL_VARIABLES:

# Git and docker params
GITCOMMIT=$(shell git rev-parse --short HEAD)
GITBRANCH=$(shell git rev-parse --abbrev-ref --short HEAD)
KALANJIYAM_VERSION=v0.1
KALANJIYAM_NAME=kalanjiyam
KALANJIYAM_IMAGE=${KALANJIYAM_NAME}:${KALANJIYAM_VERSION}-${GITBRANCH}-${GITCOMMIT}
KALANJIYAM_IMAGE_LATEST="$(KALANJIYAM_NAME)-rel:latest"

py-venv-check: 
ifeq ("$(VIRTUAL_ENV)","")
	@echo "Error! Python venv not activated. Activate venv to proceed. Run: "
	@echo "  > source env/bin/activate"
	@echo
	exit 1
endif	

DB_FILE = ${PWD}/deploy/data/database/database.db


# Setup commands
# ===============================================

# Install the repository from scratch.
# This command does NOT install data dependencies.
install:
	./scripts/install_from_scratch.sh

# Install frontend dependencies and build CSS and JS assets.
install-frontend:
	npm install
	make css-prod js-prod

# Install Python dependencies.
install-python:
	python3 -m venv env
	. env/bin/activate; pip install --upgrade pip
	. env/bin/activate; pip install -r requirements.txt

# Fetch and build all i18n files.
install-i18n: py-venv-check
	python -m kalanjiyam.scripts.fetch_i18n_files
	# Force a build with `-f`. Transifex files have a `fuzzy` annotation, so if
	# we build without this flag, then all of the files will be skipped with:
	#
	#     "catalog <file>.po" is marked as fuzzy, skipping"
	#
	# There's probably a nicer workaround for this, but `-f` works and unblocks
	# this command for now.
	pybabel compile -d kalanjiyam/translations -f

# Upgrade an existing setup.
upgrade:
	make install-frontend install-python
	. env/bin/activate; make install-i18n
	. env/bin/activate; alembic upgrade head
	. env/bin/activate; python -m kalanjiyam.seed.lookup

# Seed the database with a minimal dataset for CI. We fetch data only if it is
# hosted on GitHub. Other resources are less predictable.
db-seed-ci: py-venv-check
	python -m kalanjiyam.seed.lookup
	python -m kalanjiyam.seed.texts.gretil
	python -m kalanjiyam.seed.dcs

# Seed the database with just enough data for the devserver to be interesting.
db-seed-basic: py-venv-check
	python -m kalanjiyam.seed.lookup
	python -m kalanjiyam.seed.texts.gretil
	python -m kalanjiyam.seed.dcs
	python -m kalanjiyam.seed.dictionaries.monier

# Seed the database with all of the text, parse, and dictionary data we serve
# in production.
db-seed-all: py-venv-check
	python -m kalanjiyam.seed.lookup.role
	python -m kalanjiyam.seed.lookup.page_status
	python -m kalanjiyam.seed.texts.gretil
	python -m kalanjiyam.seed.texts.ramayana
	python -m kalanjiyam.seed.texts.mahabharata
	python -m kalanjiyam.seed.dcs
	python -m kalanjiyam.seed.dictionaries.amarakosha
	python -m kalanjiyam.seed.dictionaries.apte
	python -m kalanjiyam.seed.dictionaries.apte_sanskrit_hindi
	python -m kalanjiyam.seed.dictionaries.monier
	python -m kalanjiyam.seed.dictionaries.shabdakalpadruma
	python -m kalanjiyam.seed.dictionaries.shabdartha_kaustubha
	python -m kalanjiyam.seed.dictionaries.shabdasagara
	python -m kalanjiyam.seed.dictionaries.vacaspatyam


# Local run commands
# ===============================================

.PHONY: devserver celery redis redis-stop

# For Docker try `make mode=dev docker-start`
devserver: py-venv-check
	./node_modules/.bin/concurrently "flask run -h 0.0.0.0 -p 5000" "npx tailwindcss -i kalanjiyam/static/css/style.css -o kalanjiyam/static/gen/style.css --watch" "npx esbuild kalanjiyam/static/js/main.js --outfile=kalanjiyam/static/gen/main.js --bundle --watch --loader:.woff2=file --loader:.woff=file --loader:.ttf=file --asset-names=[name]-[hash]"
	
# Run a local Celery instance for background tasks.
celery: 
	celery -A kalanjiyam.tasks worker --loglevel=INFO --concurrency=2 --prefetch-multiplier=1 -Q default,ocr

# Start Redis server for Celery backend and broker.
redis:
	redis-server --daemonize yes

# Stop Redis server.
redis-stop:
	redis-cli shutdown

# Monitor memory usage of Celery workers and Surya OCR processes
memory-monitor:
	python scripts/monitor_memory.py

# Stop all Celery workers (useful for memory issues)
celery-stop:
	python scripts/monitor_memory.py --kill-workers

# Docker commands
# ===============================================

.PHONY: docker-setup-db docker-build docker-start docker-stop docker-logs
# Start DB using Docker.
docker-setup-db: docker-build 
ifneq ("$(wildcard $(DB_FILE))","")
	@echo "Kalanjiyam using your existing database!"
else
	@docker ${DOCKER_LOG_LEVEL} compose -p kalanjiyam-${KALANJIYAM_DEPLOYMENT_ENV} -f deploy/${KALANJIYAM_DEPLOYMENT_ENV}/docker-compose-dbsetup.yml up ${IO_REDIRECT}
	@echo "Kalanjiyam Database : ✔ "
endif
	
# Build docker image. All tag the latest to the most react image
# docker-build: lint-check
docker-build: 
	@echo "> Kalanjiyam build is in progress. Expect it to take 2-5 minutes."
	@printf "%0.s-" {1..21} && echo
	@docker build ${DOCKER_VEBOSITY} -t ${KALANJIYAM_IMAGE} -t ${KALANJIYAM_IMAGE_LATEST} -f build/containers/Dockerfile.final ${PWD} ${IO_REDIRECT}
	@echo "Kalanjiyam Image    : ✔ (${KALANJIYAM_IMAGE}, ${KALANJIYAM_IMAGE_LATEST})"

# Start Docker services.
docker-start: docker-build docker-setup-db
	@docker ${DOCKER_LOG_LEVEL} compose -p kalanjiyam-${KALANJIYAM_DEPLOYMENT_ENV} -f deploy/${KALANJIYAM_DEPLOYMENT_ENV}/docker-compose.yml up ${DOCKER_DETACH} ${IO_REDIRECT}
	@echo "Kalanjiyam WebApp   : ✔ "
	@echo "Kalanjiyam URL      : http://${KALANJIYAM_HOST_IP}:${KALANJIYAM_HOST_PORT}"
	@printf "%0.s-" {1..21} && echo
	@echo 'To stop, run "make docker-stop".'

# Stop docker services
docker-stop: 
	@docker ${DOCKER_LOG_LEVEL} compose -p kalanjiyam-${KALANJIYAM_DEPLOYMENT_ENV} -f deploy/${KALANJIYAM_DEPLOYMENT_ENV}/docker-compose.yml stop
	@docker ${DOCKER_LOG_LEVEL} compose -p kalanjiyam-${KALANJIYAM_DEPLOYMENT_ENV} -f deploy/${KALANJIYAM_DEPLOYMENT_ENV}/docker-compose.yml rm
	@echo "Kalanjiyam URL stopped"

# Show docker logs
docker-logs: 
	@docker compose -p kalanjiyam-${KALANJIYAM_DEPLOYMENT_ENV} -f deploy/${KALANJIYAM_DEPLOYMENT_ENV}/docker-compose.yml logs


# Lint commands
# ===============================================

# Link checks on Python code
py-lint: py-venv-check
	ruff . --fix
	black .

# Lint our Python and JavaScript code. Fail on any issues.
lint-check: js-lint
	black . --diff


# Test, coverage and documentation commands
# ===============================================

# Run all Python unit tests.
test: py-venv-check
	pytest .

# Run all Python unit tests with a coverage report.
# After the command completes, open "htmlcov/index.html".
coverage:
	pytest --cov=kalanjiyam --cov-report=html test/

coverage-report: coverage
	coverage report --fail-under=80

# Generate Kalanjiyam's technical documentation.
# After the command completes, open "docs/_build/index.html".
docs: py-venv-check
	cd docs && make html


# CSS commands
# ===============================================

# Run Tailwind to build our CSS, and rebuild our CSS every time a relevant file
# changes.
css-dev:
	npx tailwindcss -i ./kalanjiyam/static/css/style.css -o ./kalanjiyam/static/gen/style.css --watch

# Build CSS for production.
css-prod:
	npx tailwindcss -i ./kalanjiyam/static/css/style.css -o ./kalanjiyam/static/gen/style.css --minify


# JavaScript commands
# ===============================================

# Run esbuild to build our JavaScript, and rebuild our JavaScript every time a
# relevant file changes.
js-dev:
	npx esbuild kalanjiyam/static/js/main.js --outfile=kalanjiyam/static/gen/main.js --bundle --watch --loader:.woff2=file --loader:.woff=file --loader:.ttf=file --asset-names=[name]-[hash]

# Build JS for production.
js-prod:
	npx esbuild kalanjiyam/static/js/main.js --outfile=kalanjiyam/static/gen/main.js --bundle --minify --loader:.woff2=file --loader:.woff=file --loader:.ttf=file --asset-names=[name]-[hash]

js-test:
	npx jest

js-coverage:
	npx jest --coverage

# Lint our JavaScript code.
js-lint:
	npx eslint --fix kalanjiyam/static/js/* --ext .js,.ts

# Check our JavaScript code for type consistency.
js-check-types:
	npx tsc kalanjiyam/static/js/*.ts -noEmit


# i18n and l10n commands
# ===============================================

# Extract all translatable text from the application and save it in `messages.pot`.
babel-extract: py-venv-check
	pybabel extract --mapping babel.cfg --keywords _l --output-file messages.pot .

# Create a new translation file from `messages.pot`.
babel-init: py-venv-check
	pybabel init -i messages.pot -d kalanjiyam/translations --locale $(locale)

# Update all translation files with new text from `messages.pot`
babel-update: py-venv-check
	pybabel update -i messages.pot -d kalanjiyam/translations

# Compile all translation files.
# NOTE: you probably want `make install-i18n` instead.
babel-compile: py-venv-check
	pybabel compile -d kalanjiyam/translations

# Clean up
# ===============================================

clean:
	@rm -rf deploy/data/
	@rm -rf kalanjiyam/translations/*
