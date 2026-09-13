"""Entry point for the Raybot agent web UI.

    python run_agent.py     ->  http://localhost:5003

Needs a local Ollama with a tool-capable model pulled:

    ollama pull llama3.2:3b
"""

from raybot_agent.web import app

if __name__ == "__main__":
    app.run(port=5003, debug=False)
