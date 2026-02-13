#!/bin/bash
set -e

# --- Configuration ---
SONAR_Url="http://54.37.252.57:9000"
PROJECT_KEY="tomaasz_ocr-dashboard-v3"
COVERAGE_FILE="coverage.xml"
SONAR_COVERAGE_FILE="coverage.sonar.xml"

# --- Colors ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting SonarQube Analysis...${NC}"

# 1. Check for SONAR_TOKEN
if [ -z "$SONAR_TOKEN" ]; then
    echo -e "${RED}Error: SONAR_TOKEN environment variable is not set.${NC}"
    echo "Please export SONAR_TOKEN='your_token_here' or add it to your .env file."
    exit 1
fi

# 2. Always Regenerate Coverage (to ensure correct paths)
# Poprzednio sprawdzaliśmy if [ ! -f ]; ale stare pliki coverage z dockera psuły ścieżki.
# Teraz zawsze generujemy świeży raport lokalnie.
echo -e "${YELLOW}Running tests to generate fresh coverage report...${NC}"
# remove old file to be safe
rm -f "$COVERAGE_FILE"
pytest --cov=. --cov-report=xml --cov-report=term-missing

# 3. Prepare Coverage for Sonar (Handle Paths)
# Tworzymy kopię coverage.xml, żeby nie psuć oryginału
cp "$COVERAGE_FILE" "$SONAR_COVERAGE_FILE"
CURRENT_DIR=$(pwd)

# 4. Run Sonar Scanner
if command -v sonar-scanner &> /dev/null; then
    echo "Running local sonar-scanner..."
    
    # Jeśli mamy /usr/src/app (z dockera testowego), zamieniamy na bieżący katalog
    sed -i "s|/usr/src/app|$CURRENT_DIR|g" "$SONAR_COVERAGE_FILE"
    
    sonar-scanner \
        -Dsonar.projectKey="$PROJECT_KEY" \
        -Dsonar.host.url="$SONAR_Url" \
        -Dsonar.login="$SONAR_TOKEN" \
        -Dsonar.python.coverage.reportPaths="$SONAR_COVERAGE_FILE"
        
else
    echo "Local sonar-scanner not found. Using Docker..."
    echo -e "${YELLOW}Adapting paths for Docker Sonar Scanner (/usr/src)...${NC}"
    
    # Scanner w dockerze widzi kod w /usr/src.
    # Musimy zamienić lokalne ścieżki (PWD) na /usr/src
    sed -i "s|$CURRENT_DIR|/usr/src|g" "$SONAR_COVERAGE_FILE"
    # Oraz stare ścieżki z dockera testowego (jeśli są)
    sed -i "s|/usr/src/app|/usr/src|g" "$SONAR_COVERAGE_FILE"
    
    docker run \
        --rm \
        -e SONAR_HOST_URL="$SONAR_Url" \
        -e SONAR_TOKEN="$SONAR_TOKEN" \
        -v "$(pwd):/usr/src" \
        sonarsource/sonar-scanner-cli \
        -Dsonar.projectKey="$PROJECT_KEY" \
        -Dsonar.python.coverage.reportPaths="$SONAR_COVERAGE_FILE"
fi

# Cleanup
rm -f "$SONAR_COVERAGE_FILE"

echo -e "${GREEN}Analysis complete!${NC}"
echo "View results at: $SONAR_Url/dashboard?id=$PROJECT_KEY"
