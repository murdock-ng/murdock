.PHONY: all build-frontend clone-frontend deploy init-env

all: build-frontend deploy

init-env:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
	fi

clone-frontend:
	@if [ ! -f ./murdock-html/.env ]; then \
		git clone https://github.com/murdock-ng/murdock-html.git; \
	fi

build-frontend: init-env clone-frontend
	docker-compose run frontend-build

deploy: init-env
	docker-compose stop
	docker-compose up -d
