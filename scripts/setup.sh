#!/bin/bash
# OCR Dashboard V2 - Setup Script

set -e

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

echo "🔧 Setting up OCR Dashboard V2..."

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install dependencies
echo "📥 Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p logs data config

# Copy .env.example if .env doesn't exist
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "📝 Creating .env from .env.example..."
    cp .env.example .env
    echo "⚠️  Please edit .env with your configuration"
fi

echo "✅ Setup complete!"
echo ""
echo "To start the dashboard:"
echo "  ./scripts/start_web.sh"
