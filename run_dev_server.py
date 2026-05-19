#!/usr/bin/env python
"""Development server runner for Kalanjiyam."""

import sys
import os

# Add the parent directory to the Python path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalanjiyam import create_app

if __name__ == '__main__':
    # Create Flask app with development config
    app = create_app('development')
    
    # Run the development server
    print("Starting Kalanjiyam development server...")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, host='0.0.0.0', port=5000)
