#!/bin/bash

# Start the Kalanjiyam development server
# This script starts the Flask development server along with Tailwind CSS and esbuild watchers

set -e

echo "Starting Kalanjiyam development server..."

# Check if we're in a Docker container
if [ -f /.dockerenv ]; then
    echo "Running in Docker container..."
    
    # Dynamically load css and js changes. Docker compose attaches to the kalanjiyam/static directory on localhost.
    ./node_modules/.bin/concurrently \
        "/venv/bin/flask run -h 0.0.0.0 -p 5000" \
        "npx tailwindcss -i /app/kalanjiyam/static/css/style.css -o /app/kalanjiyam/static/gen/style.css --watch" \
        "npx esbuild /app/kalanjiyam/static/js/main.js --outfile=/app/kalanjiyam/static/gen/main.js --bundle --watch"
else
    echo "Running locally..."
    
    # Start the development server with hot reloading
    ./node_modules/.bin/concurrently \
        "flask run -h 0.0.0.0 -p 5000" \
        "npx tailwindcss -i kalanjiyam/static/css/style.css -o kalanjiyam/static/gen/style.css --watch" \
        "npx esbuild kalanjiyam/static/js/main.js --outfile=kalanjiyam/static/gen/main.js --bundle --watch"
fi