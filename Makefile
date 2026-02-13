.PHONY: sonar test

# Run tests and generate coverage
test:
	pytest --cov=. --cov-report=xml --cov-report=term-missing

# Run SonarQube analysis
sonar:
	./scripts/sonar.sh
