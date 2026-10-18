"""Compatibility facade for the existing root Flask application."""

from app import app

__all__ = ["app"]

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
