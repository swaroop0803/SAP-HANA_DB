"""
Flask web server for PrintWorks SAP AI Chatbot (Read-Only)
Wraps chatbot1 (read-only queries) with a browser-based chat UI
"""

import sys
import os

# Add parent directory so we can import chatbot1
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, request, jsonify, render_template
from chatbot1.chatbot1 import (
    llm_refine_query, parse_user_intent, llm_summarize_results,
    format_results, TOOLS, get_connection, SCHEMA
)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "Empty message"}), 400

    try:
        refined = llm_refine_query(user_input)
        tool_name, params = parse_user_intent(refined)
        tool_fn = TOOLS[tool_name]
        if tool_name == "run_custom_sql":
            results = tool_fn(params.get("sql", refined))
        else:
            results = tool_fn(**params)

        summary = llm_summarize_results(results, user_input)
        table = format_results(results)

        return jsonify({
            "refined_query": refined if refined != user_input else None,
            "tool": tool_name,
            "params": params,
            "table": table,
            "summary": summary,
            "row_count": len(results) if isinstance(results, list) else 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def health():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{SCHEMA}"."SALES_ORDERS"')
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return jsonify({"status": "ok", "orders_loaded": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)
