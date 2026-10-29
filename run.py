"""Start the web interface:  python run.py  ->  http://127.0.0.1:5050"""

import os

from debt_analysis.web.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
